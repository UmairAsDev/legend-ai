# utils/engine_utils.py

import math
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from loguru import logger

from services.em_selector import select_em_code
from services.modifier_engine import (
    assign_em_modifier,
    assign_laterality_modifiers,
    assign_multiple_procedure_modifiers,
)


# =============================================================
# CANDIDATE TRIMMING
# =============================================================

_LLM_FIELDS = {
    "code",
    "description",
    "proName",
    "associatedWithProCode",
    "type",
    "minSize",
    "maxSize",
}


def trim_for_llm(candidates: List[Dict], max_candidates: int = 15) -> List[Dict]:
    """
    Prepare the *ambiguous* candidate list for the LLM prompt.

    Called only for candidates that the deterministic selector layer could
    NOT resolve — confirmed codes bypass this function entirely and go
    directly to build_coding_prompt via the confirmed_codes parameter.

    Drops E/M and modifier entries (both are assigned post-LLM).
    Deduplicates by code.
    Strips internal/scoring fields.
    Caps at max_candidates to bound token usage.
    """
    seen: set = set()
    result: List[Dict] = []

    for c in candidates:
        if c.get("type") in ("em", "modifier"):
            continue
        if c.get("confidence") == "confirmed":
            continue  # already in the confirmed_codes path

        code = str(c.get("code", "")).strip()
        if not code or code in seen:
            continue

        seen.add(code)
        result.append({k: v for k, v in c.items() if k in _LLM_FIELDS})

    return result[:max_candidates]


# =============================================================
# SERIALIZATION / NOTE CLEANING
# =============================================================

def serialize_data(obj):
    """Convert non-serializable types (datetime, Decimal) for JSON output."""
    if isinstance(obj, dict):
        return {k: serialize_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_data(i) for i in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def clean_note_data(note: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the note fields the pipeline and LLM need."""
    allowed_fields = [
        "complaints",
        "pastHistory",
        "assesment",
        "reviewofsystem",
        "currentmedication",
        "procedure",
        "biopsyNotes",
        "mohsNotes",
        "patientSummary",
        "diagnoses",
        "PlaceOfService",
    ]
    return {k: note.get(k) for k in allowed_fields}


# =============================================================
# AGGREGATION HELPERS
# =============================================================

def aggregate_closures(parsed: Dict) -> Dict:
    """Sum closure sizes per group key before retrieval."""
    logger.info("Aggregating closures")

    grouped: Dict = {}
    for sec in parsed.get("closure_sections", []):
        key = sec.get("group_key") or f"{sec['type']}_unknown"
        grouped.setdefault(key, {
            "type": sec["type"],
            "group_key": key,
            "total_size": 0.0,
            "locations": [],
        })
        grouped[key]["total_size"] += float(sec.get("size") or 0)
        grouped[key]["locations"].append(sec.get("location"))

    parsed["closure_aggregated"] = list(grouped.values())

    for sec in parsed.get("closure_sections", []):
        logger.debug(f"closure raw  size={sec['size']} loc={sec['location']} type={sec['type']}")
    for g in parsed["closure_aggregated"]:
        logger.debug(f"closure agg  group={g['group_key']} total={g['total_size']} locs={g['locations']}")

    return parsed


def aggregate_shave_removals(parsed: Dict) -> Dict:
    """Group shave removal sections by location group and size."""
    grouped: Dict = {}
    for sec in parsed.get("shave_removal_sections", []):
        key = (sec.get("location_group"), sec.get("size"))
        grouped.setdefault(key, {
            "location_group": sec.get("location_group"),
            "size": sec.get("size"),
            "quantity": 0,
            "locations": [],
        })
        grouped[key]["quantity"] += 1
        grouped[key]["locations"].append(sec.get("location"))

    parsed["shave_removal_aggregated"] = list(grouped.values())
    logger.info(f"Aggregated shave removals: {parsed['shave_removal_aggregated']}")
    return parsed


def aggregate_chemical_peels(parsed: Dict) -> Dict:
    """Group chemical peel sections by type, method, and location."""
    logger.info("Aggregating chemical peels")

    grouped: Dict = {}
    for sec in parsed.get("chemical_peel_sections", []):
        key = f"{sec.get('type')}_{sec.get('method')}_{sec.get('location')}"
        grouped.setdefault(key, {
            "type": sec.get("type"),
            "method": sec.get("method"),
            "location": sec.get("location"),
            "quantity": 0,
            "sections": [],
        })
        grouped[key]["quantity"] += int(sec.get("quantity") or 1)
        grouped[key]["sections"].append(sec)

    parsed["chemical_peel_aggregated"] = list(grouped.values())
    logger.info(f"Aggregated chemical peels: {len(parsed['chemical_peel_aggregated'])}")
    return parsed


# =============================================================
# ENFORCEMENT: EXCISION QUANTITY
# =============================================================

def enforce_excision_quantity(parsed: Dict, llm_output: Dict) -> Dict:
    try:
        for sec in parsed.get("excision_sections", []):
            qty = sec.get("quantity", 1)
            if qty > 1:
                for cpt in llm_output["codes"]["cpt_codes"]:
                    if cpt["code"].startswith("116"):
                        cpt["quantity"] = str(qty)
        return llm_output
    except Exception as e:
        logger.warning(f"Excision quantity enforcement failed: {e}")
        return llm_output


# =============================================================
# ENFORCEMENT: CLOSURE ADD-ONS
# =============================================================

def _build_closure_hierarchy(candidates: List[Dict]) -> Dict:
    hierarchy: Dict = {}
    for c in candidates:
        parent = c.get("associatedWithProCode")
        if parent:
            parent = str(parent).strip().removesuffix(".0")
            if parent not in ("", "0", "None", "null"):
                hierarchy.setdefault(parent, []).append(c)
    logger.debug(f"Closure hierarchy: {hierarchy}")
    return hierarchy


def _select_primary_code(candidates: List[Dict], total_size: float):
    base_codes = [
        c for c in candidates
        if str(c.get("associatedWithProCode") or "").strip() in ("", "None")
    ]
    base_codes.sort(key=lambda x: float(x.get("maxSize") or 0))

    logger.debug(
        f"Base candidates: {[(c['code'], c.get('minSize'), c.get('maxSize')) for c in base_codes]}"
    )

    for c in base_codes:
        if total_size <= float(c.get("maxSize") or 0):
            return c
    return base_codes[-1] if base_codes else None


def _calculate_addon_units(addon_code: Dict, total_size: float, base_max: float) -> int:
    extra = total_size - base_max
    if extra <= 0:
        return 0
    match = re.search(r"each additional (\d+\.?\d*)", (addon_code.get("description") or "").lower())
    step = float(match.group(1)) if match else 5.0
    return math.ceil(extra / step)


def enforce_closure_addon(parsed: Dict, candidates: List[Dict], llm_output: Dict) -> Dict:
    try:
        logger.info("Enforcing closure add-ons")

        closure_groups = parsed.get("closure_aggregated", [])
        if not closure_groups:
            return llm_output

        # If LLM already assigned closures, do not override
        if any(str(c["code"]).startswith(("120", "131")) for c in llm_output["codes"]["cpt_codes"]):
            logger.info("LLM already assigned closure codes — skipping enforcement")
            return llm_output

        closure_candidates = [
            c for c in candidates
            if str(c.get("code", "")).startswith(("120", "131"))
        ]

        logger.debug(f"Closure candidates: {[(c['code'], c.get('associatedWithProCode')) for c in closure_candidates]}")

        hierarchy = _build_closure_hierarchy(closure_candidates)
        final_codes: List[Dict] = []

        for group in closure_groups:
            total_size = group["total_size"]
            ctype = group["type"]
            location_group = group.get("group_key", "").split("_")[-1]

            logger.info(f"Closure group  size={total_size}  type={ctype}")

            type_candidates = [
                c for c in closure_candidates
                if (ctype == "complex" and str(c["code"]).startswith("131"))
                or (ctype == "intermediate" and str(c["code"]).startswith("120"))
            ]

            location_keywords = {
                "extremities": ["scalp", "arm", "leg"],
                "critical": ["nose", "lip", "ear", "eyelid"],
                "high_risk": ["face", "hand", "foot", "neck", "chin", "cheek"],
                "trunk": ["trunk", "back", "chest", "abdomen"],
            }
            kws = location_keywords.get(location_group)
            filtered = [
                c for c in type_candidates
                if not kws or any(k in (c.get("description") or "").lower() for k in kws)
            ]

            logger.debug(f"Filtered closure candidates: {[c['code'] for c in filtered]}")

            primary = _select_primary_code(filtered, total_size)
            if not primary:
                logger.warning("No primary closure match found")
                continue

            primary_code = str(primary["code"])
            base_max = float(primary.get("maxSize") or 0)

            logger.info(f"Primary closure  code={primary_code}  total={total_size}  base_max={base_max}")

            final_codes.append({
                "code": primary_code,
                "description": primary["description"],
                "modifier": None,
                "linked_dx": [],
                "quantity": "1",
            })

            for addon in hierarchy.get(primary_code, []):
                units = _calculate_addon_units(addon, total_size, base_max)
                if units > 0:
                    logger.info(f"Closure add-on  code={addon['code']}  units={units}")
                    final_codes.append({
                        "code": addon["code"],
                        "description": addon["description"],
                        "modifier": None,
                        "linked_dx": [],
                        "quantity": str(units),
                    })

        llm_output["codes"]["cpt_codes"].extend(final_codes)
        logger.info(f"Closure enforcement complete: {final_codes}")
        return llm_output

    except Exception as e:
        logger.exception(f"Closure enforcement failed: {e}")
        return llm_output


# =============================================================
# ENFORCEMENT: DESTRUCTION QUANTITIES
# =============================================================

def enforce_destruction_quantity(
    parsed: Dict,
    retrieved_candidates: List[Dict],
    llm_output: Dict,
) -> Dict:
    try:
        logger.info("Enforcing destruction quantities")

        destruction_sections = parsed.get("destruction_sections", [])
        if not destruction_sections:
            return llm_output

        cpt_codes = llm_output.get("codes", {}).get("cpt_codes", [])
        if not cpt_codes:
            logger.warning("No CPT codes in LLM output — skipping destruction enforcement")
            return llm_output

        candidate_map = {
            str(c.get("code", "")).strip(): c
            for c in retrieved_candidates
            if c.get("code")
        }
        logger.debug(f"Destruction candidate map: {len(candidate_map)} entries")

        for cpt in cpt_codes:
            code = str(cpt.get("code", "")).strip()
            if not code:
                continue

            candidate = candidate_map.get(code)
            if not candidate:
                continue

            source = candidate.get("source", "")
            if not source.startswith("destruction_"):
                continue

            matched_section = next(
                (s for s in destruction_sections
                 if f"destruction_{s.get('destruction_type')}" == source),
                None,
            )
            if not matched_section:
                logger.warning(f"No destruction section matched for CPT {code}")
                continue

            lesion_qty = matched_section.get("quantity") or 1
            associated = candidate.get("associatedWithProCode")

            logger.debug(f"Destruction CPT={code}  qty={lesion_qty}  addon={bool(associated)}")

            if associated is None and code == "17000":
                cpt["quantity"] = "1"
            else:
                cpt["quantity"] = str(lesion_qty)

        logger.info("Destruction quantity enforcement complete")
        return llm_output

    except Exception as e:
        logger.exception(f"Destruction quantity enforcement failed: {e}")
        return llm_output


# =============================================================
# CONFIRMED CODE INJECTION
# =============================================================

def enforce_confirmed_codes(
    candidates: List[Dict],
    llm_output: Dict[str, Any],
    note: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Guarantee that every selector-confirmed code appears in the final output.

    The LLM is instructed to include confirmed codes but occasionally drops
    one — especially in complex multi-procedure notes.  This function injects
    any missing confirmed code back after the LLM step, using the note's
    diagnoses field as a fallback for linked_dx.
    """
    from services.mdm_classifier import extract_diagnoses_from_note

    confirmed = [c for c in candidates if c.get("confidence") == "confirmed"]
    if not confirmed:
        return llm_output

    cpt_codes = llm_output.get("codes", {}).get("cpt_codes", [])
    existing = {str(c.get("code", "")).strip() for c in cpt_codes}
    fallback_dx = extract_diagnoses_from_note(note) if note else []

    for conf in confirmed:
        code = str(conf.get("code", "")).strip()
        if not code or code in existing:
            continue
        cpt_codes.append({
            "code": code,
            "description": conf.get("description", ""),
            "modifier": None,
            "linked_dx": fallback_dx,
            "quantity": str(conf.get("quantity", "1")),
        })
        existing.add(code)
        logger.info(f"Injected missing confirmed code: {code}")

    llm_output["codes"]["cpt_codes"] = cpt_codes
    return llm_output


# =============================================================
# CHARGE-PER-UNIT ENRICHMENT
# =============================================================

def enrich_with_charges(llm_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add charge_per_unit flag to every CPT and E/M code in the output.
    Reads from proCodeList.csv via charge_lookup (cached after first load).
    """
    from services.charge_lookup import is_charge_per_unit

    codes = llm_output.get("codes", {})

    for cpt in codes.get("cpt_codes", []):
        cpt["charge_per_unit"] = is_charge_per_unit(cpt.get("code", ""))

    em = codes.get("em_code", {})
    if em.get("code"):
        em["charge_per_unit"] = is_charge_per_unit(em["code"])

    return llm_output


# =============================================================
# LLM OUTPUT NORMALISATION
# =============================================================

def normalize_llm_output(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Guarantee the canonical shape that all downstream enforcement functions expect:
      {
        "patient_summary": str,
        "codes": {
          "cpt_codes": [...],
          "em_code": {...}
        },
        "justification": {}
      }

    The LLM occasionally returns a flat structure (cpt_codes / em_code at the top
    level) or uses prompt-section names as output keys (confirmed_codes,
    procedure_codes).  This function absorbs all variants without raising.
    """
    if not isinstance(raw, dict):
        raw = {}

    # Already canonical
    if "codes" in raw and isinstance(raw["codes"], dict) and "cpt_codes" in raw["codes"]:
        return raw

    # Gather cpt_codes from any known variant key
    cpt_codes = (
        raw.get("codes", {}).get("cpt_codes")
        or raw.get("cpt_codes")
        or raw.get("confirmed_codes")
        or raw.get("procedure_codes")
        or []
    )

    # Gather em_code
    em_code = (
        raw.get("codes", {}).get("em_code")
        or raw.get("em_code")
        or {"code": "", "modifier": None, "linked_dx": []}
    )

    return {
        "patient_summary": raw.get("patient_summary", ""),
        "codes": {
            "cpt_codes": cpt_codes if isinstance(cpt_codes, list) else [],
            "em_code": em_code if isinstance(em_code, dict) else {"code": "", "modifier": None, "linked_dx": []},
        },
    }


# =============================================================
# ENFORCEMENT: E/M CODE AND MODIFIERS
# =============================================================

def enforce_em_and_modifiers(
    parsed: Dict[str, Any],
    llm_output: Dict[str, Any],
    note: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Post-LLM deterministic enforcement:
    1. Select E/M code using explicit code → documented time → explicit level → MDM level.
    2. Check if E/M is billable alongside the assigned CPT codes.
    3. Assign modifier -25 (or -57) to the E/M code.
    4. Assign LT/RT laterality modifiers to eligible CPT codes.
    5. Assign modifier -51 to secondary CPT codes when multiple procedures are billed.

    For E/M-only notes (no CPT codes), linked_dx is populated from the note's
    diagnoses field so the E/M code is fully coded without LLM involvement.
    """
    from services.mdm_classifier import extract_diagnoses_from_note

    try:
        em_data = parsed.get("em_data", {})
        patient_type = em_data.get("patient_type")
        encounter_time = em_data.get("encounter_time")
        em_level = em_data.get("em_level")
        mdm_level = em_data.get("mdm_level")
        explicit_em_code = em_data.get("explicit_em_code")

        cpt_codes: List = llm_output.get("codes", {}).get("cpt_codes", [])
        has_procedures = bool(cpt_codes)

        def apply_cpt_modifiers() -> None:
            llm_output["codes"]["cpt_codes"] = assign_laterality_modifiers(
                llm_output["codes"]["cpt_codes"], parsed
            )
            llm_output["codes"]["cpt_codes"] = assign_multiple_procedure_modifiers(
                llm_output["codes"]["cpt_codes"]
            )

        # Determine E/M code — priority chain:
        # explicit code > documented time > explicit level > MDM-inferred level
        em_row = None
        if explicit_em_code:
            em_row = {"enmCode": explicit_em_code, "enmCodeDesc": "Office visit"}
            logger.info(f"E/M from note verbatim: {explicit_em_code}")
        elif patient_type:
            em_row = select_em_code(patient_type, encounter_time, em_level)
            if not em_row and mdm_level is not None:
                em_row = select_em_code(patient_type, em_level=mdm_level)
                if em_row:
                    logger.info(f"E/M selected via MDM level {mdm_level}: {em_row['enmCode']}")

        if not em_row:
            logger.info("Insufficient E/M signals — keeping LLM em_code output")
            apply_cpt_modifiers()
            return llm_output

        # Build linked_dx: prefer LLM-provided, fall back to note diagnoses field
        linked_dx = llm_output.get("codes", {}).get("em_code", {}).get("linked_dx") or []
        if not linked_dx and note:
            linked_dx = extract_diagnoses_from_note(note)
            if linked_dx:
                logger.info(f"E/M linked_dx from diagnoses field: {linked_dx}")

        em_code_dict = {"code": em_row["enmCode"], "modifier": None, "linked_dx": linked_dx}
        em_code_dict = assign_em_modifier(em_code_dict, has_procedures, is_surgery_decision=False)

        llm_output["codes"]["em_code"] = em_code_dict
        logger.info(f"E/M assigned: {em_code_dict['code']}  modifier={em_code_dict['modifier']}  dx={linked_dx}")

        apply_cpt_modifiers()
        logger.info("E/M and modifier enforcement complete")
        return llm_output

    except Exception as e:
        logger.exception(f"enforce_em_and_modifiers failed: {e}")
        return llm_output


# =============================================================
# MERGE: REGEX PARSED + LLM EXTRACTED SECTIONS
# =============================================================

# All section keys that ClinicalParser.parse() and ProcedureExtractionOutput produce
_SECTION_KEYS = [
    "excision_sections",
    "biopsy_sections",
    "destruction_sections",
    "shave_removal_sections",
    "mohs_sections",
    "closure_sections",
    "srt_sections",
    "debridement_sections",
    "xtrac_sections",
    "ipl_sections",
    "laser_treatment_sections",
    "filler_sections",
    "filler_material_sections",
    "chemical_peel_sections",
]

# Mapping from section key → has_ flag key
_HAS_FLAGS = {k: f"has_{k.replace('_sections', '')}" for k in _SECTION_KEYS}


def merge_parsed_results(
    regex_parsed: Dict[str, Any],
    llm_extracted: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Merge regex-parsed sections with LLM-extracted sections.

    Strategy (per section type):
      - Regex found results  → use regex (deterministic, reliable)
      - Regex empty, LLM has results → use LLM extraction
      - Both empty → empty

    Returns:
      merged   : combined parsed dict (same schema as ClinicalParser.parse())
      sources  : per-section source tag ("regex" | "llm" | "empty")
    """
    merged = dict(regex_parsed)
    sources: Dict[str, str] = {}

    for section_key in _SECTION_KEYS:
        has_key = _HAS_FLAGS[section_key]

        regex_sections = regex_parsed.get(section_key) or []
        llm_sections = _extract_llm_sections(llm_extracted, section_key)

        if regex_sections:
            merged[section_key] = regex_sections
            merged[has_key] = True
            sources[section_key] = "regex"

        elif llm_sections:
            normalised = _normalise_llm_sections(section_key, llm_sections)
            merged[section_key] = normalised
            merged[has_key] = bool(normalised)
            sources[section_key] = "llm" if normalised else "empty"
            if normalised:
                logger.info(
                    f"merge: {section_key} filled by LLM "
                    f"({len(normalised)} section(s))"
                )

        else:
            merged[section_key] = []
            merged[has_key] = False
            sources[section_key] = "empty"

    # Merge em_data field-by-field (regex wins per field)
    merged["em_data"] = _merge_em_data(
        regex_parsed.get("em_data", {}),
        llm_extracted.get("em_data", {}),
    )

    # Attach unresolved_procedures from LLM output
    unresolved = llm_extracted.get("unresolved_procedures") or []
    if unresolved:
        merged["unresolved_procedures"] = [
            u if isinstance(u, dict) else u.dict()
            for u in unresolved
        ]
        logger.info(f"merge: {len(unresolved)} unresolved procedure(s) from LLM")
    else:
        merged.setdefault("unresolved_procedures", [])

    # Re-run aggregation on merged closure/shave/chemical sections
    # so downstream selectors get the aggregated views
    from utils.engine_utils import aggregate_closures, aggregate_shave_removals, aggregate_chemical_peels
    merged = aggregate_closures(merged)
    merged = aggregate_shave_removals(merged)
    merged = aggregate_chemical_peels(merged)

    confirmed_count = sum(1 for v in sources.values() if v == "regex")
    llm_count = sum(1 for v in sources.values() if v == "llm")
    logger.info(
        f"merge complete: {confirmed_count} regex sections, "
        f"{llm_count} LLM-filled sections"
    )

    return merged, sources


def _extract_llm_sections(
    llm_extracted: Dict[str, Any],
    section_key: str,
) -> List[Dict]:
    """Pull section list from LLM output, converting Pydantic models to dicts."""
    raw = llm_extracted.get(section_key) or []
    result = []
    for item in raw:
        if isinstance(item, dict):
            result.append(item)
        elif hasattr(item, "model_dump"):
            result.append(item.model_dump())
        elif hasattr(item, "dict"):
            result.append(item.dict())
    return result


def _normalise_llm_sections(
    section_key: str,
    sections: List[Dict],
) -> List[Dict]:
    """
    Post-process LLM-extracted sections so they are compatible with existing
    selectors and retrieval methods that were written for regex-parsed dicts.
    """
    normalised = []

    for sec in sections:
        sec = dict(sec)

        # Ensure 'text' field exists (selectors sometimes scan it for keywords)
        sec.setdefault("text", "")

        # Biopsy: ensure method keywords appear in text so _retrieve_biopsy works
        if section_key == "biopsy_sections":
            method_words = {"punch", "tangential", "shave", "incisional"}
            text_lower = sec.get("text", "").lower()
            if not any(w in text_lower for w in method_words):
                # Try to get method from other fields / text
                pass  # the text field was already instructed to include these

        # Closure: compute group_key if missing
        if section_key == "closure_sections":
            ctype = sec.get("type")
            loc_group = sec.get("location_group")
            if ctype and loc_group and not sec.get("group_key"):
                sec["group_key"] = f"{ctype}_{loc_group}"

        normalised.append(sec)

    return normalised


def _merge_em_data(
    regex_em: Dict[str, Any],
    llm_em: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Field-by-field merge: regex value wins if non-None.
    LLM value used when regex returned None for that field.
    """
    fields = ["patient_type", "encounter_time", "em_level", "explicit_em_code"]
    merged: Dict[str, Any] = {}

    for field in fields:
        regex_val = regex_em.get(field)
        llm_val = llm_em.get(field) if isinstance(llm_em, dict) else None
        merged[field] = regex_val if regex_val is not None else llm_val

    # mdm_level always comes from the deterministic MDM classifier, never LLM
    merged["mdm_level"] = regex_em.get("mdm_level")

    if any(
        merged[f] != regex_em.get(f)
        for f in fields
        if merged[f] is not None
    ):
        logger.info(f"merge: em_data enriched by LLM — {merged}")

    return merged
