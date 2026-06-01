# utils/engine_utils.py

from datetime import datetime
from decimal import Decimal
from typing import Dict, Any
import math, re

from loguru import logger

# =========================
# 🔹 UTILITIES
# =========================

def serialize_data(obj):
    """
    Convert non-serializable types (datetime, Decimal)
    """
    if isinstance(obj, dict):
        return {k: serialize_data(v) for k, v in obj.items()}

    elif isinstance(obj, list):
        return [serialize_data(i) for i in obj]

    elif isinstance(obj, datetime):
        return obj.isoformat()

    elif isinstance(obj, Decimal):
        return float(obj)  # 🔥 FIX

    return obj


def clean_note_data(note: Dict[str, Any]):
    """
    Keep only relevant fields for LLM
    """
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


def enforce_excision_quantity(parsed, llm_output):
    try:
        exc_sections = parsed.get("excision_sections", [])

        for sec in exc_sections:
            qty = sec.get("quantity", 1)

            if qty > 1:
                for cpt in llm_output["codes"]["cpt_codes"]:
                    # match malignant excision (116xx)
                    if cpt["code"].startswith("116"):
                        cpt["quantity"] = str(qty)

        return llm_output

    except Exception as e:
        logger.warning(f"⚠️ Enforcement failed: {e}")
        return llm_output
    

# =========================================================
# 🔴 CLOSURE AGGREGATION
# =========================================================
def aggregate_closures(parsed):
    logger.info("🔧 Aggregating closures...")

    grouped = {}

    for sec in parsed.get("closure_sections", []):
        key = sec.get("group_key") or f"{sec['type']}_unknown"

        grouped.setdefault(key, {
            "type": sec["type"],
            "group_key": key,
            "total_size": 0.0,
            "locations": []
        })

        grouped[key]["total_size"] += float(sec.get("size") or 0)
        grouped[key]["locations"].append(sec.get("location"))

    parsed["closure_aggregated"] = list(grouped.values())

    # 🔴 CRITICAL DEBUG LOG
    logger.info("🧾 ===== CLOSURE DEBUG =====")

    for sec in parsed.get("closure_sections", []):
        logger.info(
            f"RAW → size={sec['size']} | loc={sec['location']} | type={sec['type']}"
        )

    for g in parsed["closure_aggregated"]:
        logger.info(
            f"AGG → group={g['group_key']} | total={g['total_size']} | locs={g['locations']}"
        )

    logger.info("🧾 ==========================")

    return parsed


# =========================================================
# 🔴 GENERIC ADD-ON / CLOSURE HELPERS
# =========================================================
def _normalize_code(value):
    if value is None:
        return ""
    code = str(value).strip()
    return code[:-2] if code.endswith(".0") else code


def _dedupe_preserve_order(values):
    seen = set()
    out = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _normalize_dx_list(dx_values):
    if not dx_values:
        return []
    if isinstance(dx_values, str):
        dx_values = [dx_values]
    return _dedupe_preserve_order(
        [str(dx).strip() for dx in dx_values if str(dx).strip()]
    )


def _extract_linked_dx(entry):
    return _normalize_dx_list(entry.get("linked_dx") or [])


def _upsert_cpt_entry(cpt_codes, entry):
    """
    Replace existing entry by code, otherwise append.
    """
    code = _normalize_code(entry.get("code"))
    entry = {**entry, "code": code}

    for i, existing in enumerate(cpt_codes):
        if _normalize_code(existing.get("code")) == code:
            cpt_codes[i] = entry
            return

    cpt_codes.append(entry)


def build_closure_hierarchy(candidates):
    """
    Map parent code -> child add-on codes.
    Example:
      13121 -> [13122]
    """
    hierarchy = {}

    for c in candidates:
        parent = c.get("associatedWithProCode") or c.get("associatedwithprocode")

        if parent:
            parent = _normalize_code(parent)
            hierarchy.setdefault(parent, []).append(c)

    logger.info(f"🧬 Hierarchy built: {hierarchy}")
    return hierarchy


def select_primary_code(candidates, total_size):
    """
    Pick the smallest base code that can hold the total size.
    Base code = code with no parent.
    """
    base_codes = [
        c for c in candidates
        if not c.get("associatedWithProCode")
        and not c.get("associatedwithprocode")
    ]

    base_codes = sorted(
        base_codes,
        key=lambda x: float(x.get("maxSize") or 0)
    )

    logger.info(
        f"🧪 Base candidates: "
        f"{[(c['code'], c.get('minSize'), c.get('maxSize')) for c in base_codes]}"
    )

    selected = None

    for c in base_codes:
        max_s = float(c.get("maxSize") or 0)
        if total_size <= max_s:
            selected = c
            break

    if not selected and base_codes:
        selected = base_codes[-1]

    return selected


def calculate_addon_units(addon_code, total_size, base_max):
    extra = total_size - base_max
    if extra <= 0:
        return 0

    desc = (addon_code.get("description") or "").lower()
    match = re.search(r"each additional (\d+\.?\d*)", desc)
    step = float(match.group(1)) if match else 5

    return math.ceil(extra / step)


def enforce_closure_addon(parsed, candidates, llm_output):
    """
    Deterministically enforce closure primary + add-on codes.

    Rules:
    - Use closure_aggregated only
    - If add-on exists, copy linked_dx from primary
    - Patch justification["closure"] to reflect primary/add-on outcome
    - Keep existing LLM output when valid, but normalize it
    """
    try:
        logger.info("🔧 Enforcing closure add-ons (DETERMINISTIC MODE)...")

        closure_groups = parsed.get("closure_aggregated", [])
        if not closure_groups:
            return llm_output

        codes_block = llm_output.setdefault("codes", {})
        cpt_codes = codes_block.setdefault("cpt_codes", [])
        justification = llm_output.setdefault("justification", {})

        # Only closure candidates
        closure_candidates = [
            c for c in candidates
            if _normalize_code(c.get("code")).startswith(("120", "131"))
        ]

        if not closure_candidates:
            logger.warning("⚠️ No closure candidates found")
            return llm_output

        logger.info(
            f"🧪 FINAL candidates → "
            f"{[(c['code'], c.get('associatedWithProCode') or c.get('associatedwithprocode')) for c in closure_candidates]}"
        )

        hierarchy = build_closure_hierarchy(closure_candidates)
        existing_closure_entries = [
            c for c in cpt_codes
            if _normalize_code(c.get("code")).startswith(("120", "131"))
        ]

        # Helper: get a dx set from any existing closure entry
        def get_group_dx(primary_code, filtered_candidates):
            primary_code = _normalize_code(primary_code)

            # Prefer existing primary entry
            for entry in existing_closure_entries:
                if _normalize_code(entry.get("code")) == primary_code:
                    dx = _extract_linked_dx(entry)
                    if dx:
                        return dx

            # Then any existing closure entry
            for entry in existing_closure_entries:
                dx = _extract_linked_dx(entry)
                if dx:
                    return dx

            # Then any LLM code with closure prefix
            for entry in cpt_codes:
                if _normalize_code(entry.get("code")).startswith(("120", "131")):
                    dx = _extract_linked_dx(entry)
                    if dx:
                        return dx

            # Final fallback: empty
            return []

        # Helper: filter candidates by type + anatomical group
        def filter_group_candidates(ctype, location_group):
            type_candidates = [
                c for c in closure_candidates
                if (
                    (ctype == "complex" and _normalize_code(c["code"]).startswith("131")) or
                    (ctype == "intermediate" and _normalize_code(c["code"]).startswith("120")) or
                    (ctype == "adjacent" and _normalize_code(c["code"]).startswith("140"))
                )
            ]

            filtered = []
            for c in type_candidates:
                desc = (c.get("description") or "").lower()

                if location_group == "extremities":
                    if not any(k in desc for k in ["scalp", "arm", "leg"]):
                        continue

                elif location_group == "critical":
                    if not any(k in desc for k in ["nose", "lip", "ear", "eyelid"]):
                        continue

                elif location_group == "high_risk":
                    if not any(k in desc for k in [
                        "face", "hand", "foot", "neck", "chin", "cheek"
                    ]):
                        continue

                elif location_group == "trunk":
                    if not any(k in desc for k in ["trunk", "back", "chest", "abdomen"]):
                        continue

                filtered.append(c)

            return filtered or type_candidates or closure_candidates

        for group in closure_groups:
            total_size = float(group.get("total_size") or 0)
            ctype = (group.get("type") or "").lower().strip()
            location_group = (group.get("group_key") or "").split("_")[-1].lower().strip()

            logger.info(f"📏 Closure group → size={total_size}, type={ctype}, location_group={location_group}")

            filtered = filter_group_candidates(ctype, location_group)
            logger.info(f"🎯 Filtered candidates: {[c['code'] for c in filtered]}")

            primary = select_primary_code(filtered, total_size)
            if not primary:
                logger.warning("⚠️ No primary closure match")
                continue

            primary_code = _normalize_code(primary["code"])
            base_max = float(primary.get("maxSize") or 0)
            group_dx = get_group_dx(primary_code, filtered)

            logger.info(
                f"🧠 PRIMARY SELECTION → total={total_size}, "
                f"selected={primary_code}, base_max={base_max}, dx={group_dx}"
            )

            # Upsert primary
            _upsert_cpt_entry(cpt_codes, {
                "code": primary_code,
                "description": primary["description"],
                "modifier": None,
                "linked_dx": group_dx,
                "quantity": "1"
            })

            addon_results = []

            # Add-ons from hierarchy
            for addon in hierarchy.get(primary_code, []):
                addon_code = _normalize_code(addon["code"])
                units = calculate_addon_units(addon, total_size, base_max)

                if units > 0:
                    logger.info(f"➕ ADDON → {addon_code} units={units}")

                    addon_entry = {
                        "code": addon_code,
                        "description": addon["description"],
                        "modifier": None,
                        "linked_dx": group_dx,
                        "quantity": str(units)
                    }
                    _upsert_cpt_entry(cpt_codes, addon_entry)
                    addon_results.append({
                        "code": addon_code,
                        "quantity": str(units)
                    })

            # Patch closure justification
            closure_just = justification.setdefault("closure", {})
            closure_just["total_size"] = total_size
            closure_just["type"] = ctype
            closure_just["location_group"] = location_group
            closure_just["cpt_code"] = primary_code  # backward compatibility
            closure_just["cpt_primary"] = primary_code
            closure_just["cpt_addon"] = addon_results[0]["code"] if addon_results else None
            closure_just["addon_units"] = int(addon_results[0]["quantity"]) if addon_results else 0
            closure_just["cpt_addons"] = addon_results
            closure_just["linked_dx"] = group_dx

        # Final normalization: ensure all closure CPTs have linked_dx if possible
        closure_dx = []
        for c in cpt_codes:
            code = _normalize_code(c.get("code"))
            if code.startswith(("120", "131")):
                dx = _extract_linked_dx(c)
                if dx:
                    closure_dx = dx
                    break

        if closure_dx:
            for c in cpt_codes:
                code = _normalize_code(c.get("code"))
                if code.startswith(("120", "131")) and not _extract_linked_dx(c):
                    c["linked_dx"] = closure_dx

        logger.info(
            f"✅ Final closure codes: "
            f"{[(c['code'], c.get('linked_dx'), c.get('quantity')) for c in cpt_codes if _normalize_code(c.get('code')).startswith(('120','131'))]}"
        )

        return llm_output

    except Exception as e:
        logger.exception(f"❌ Closure enforcement failed: {e}")
        return llm_output
    

# =========================================================
# 🔴 ENFORCE DESTRUCTION QUANTITIES
# =========================================================
def enforce_destruction_quantity(
    parsed,
    retrieved_candidates,
    llm_output
):

    try:

        logger.info("🔧 Enforcing destruction quantities...")

        destruction_sections = parsed.get("destruction_sections", [])

        if not destruction_sections:
            logger.info("ℹ️ No destruction sections found")
            return llm_output

        cpt_codes = llm_output.get("codes", {}).get("cpt_codes", [])

        if not cpt_codes:
            logger.warning("⚠️ No CPT codes found in LLM output")
            return llm_output

        # -------------------------------------------------
        # 🔴 BUILD CPT LOOKUP
        # -------------------------------------------------
        candidate_map = {}

        for c in retrieved_candidates:

            code = str(c.get("code", "")).strip()

            if code:
                candidate_map[code] = c

        logger.info(
            f"📦 Candidate lookup built: {len(candidate_map)} CPTs"
        )

        # -------------------------------------------------
        # 🔴 PROCESS EACH CPT
        # -------------------------------------------------
        for cpt in cpt_codes:

            code = str(cpt.get("code", "")).strip()

            if not code:
                continue

            candidate = candidate_map.get(code)

            if not candidate:
                continue

            source = candidate.get("source", "")

            # only destruction
            if not source.startswith("destruction_"):
                continue

            logger.info(
                f"🔍 Processing destruction CPT: {code}"
            )

            # -------------------------------------------------
            # 🔴 MATCH SECTION
            # -------------------------------------------------
            matched_section = None

            for sec in destruction_sections:

                destruction_type = sec.get("destruction_type")

                expected_source = f"destruction_{destruction_type}"

                if expected_source == source:
                    matched_section = sec
                    break

            if not matched_section:
                logger.warning(
                    f"⚠️ No destruction section matched for CPT {code}"
                )
                continue

            lesion_qty = matched_section.get("quantity") or 1

            associated = candidate.get("associatedWithProCode")

            logger.info(
                f"📊 CPT={code} | "
                f"qty_from_parser={lesion_qty} | "
                f"addon={bool(associated)}"
            )

            # -------------------------------------------------
            # 🔴 DPM PRIMARY LOGIC
            # -------------------------------------------------
            if associated is None:

                # primary DPM add-on logic
                if code == "17000":

                    logger.info(
                        "✅ DPM primary detected → forcing qty=1"
                    )

                    cpt["quantity"] = "1"

                else:

                    logger.info(
                        f"✅ Standard destruction CPT → "
                        f"forcing qty={lesion_qty}"
                    )

                    cpt["quantity"] = str(lesion_qty)

            # -------------------------------------------------
            # 🔴 ADD-ON LOGIC
            # -------------------------------------------------
            else:

                logger.info(
                    f"✅ DPM add-on CPT detected → "
                    f"forcing qty={lesion_qty}"
                )

                cpt["quantity"] = str(lesion_qty)

        logger.success(
            "✅ Destruction quantity enforcement completed"
        )

        return llm_output

    except Exception as e:
        logger.exception(
            f"❌ Destruction quantity enforcement failed: {e}"
        )

        return llm_output
    

# =========================================================
# 🔹 AGGREGATE SHAVE REMOVALS
# =========================================================
def aggregate_shave_removals(parsed):

    grouped = {}

    for sec in parsed.get("shave_removal_sections", []):

        key = (
            sec.get("location_group"),
            sec.get("size")
        )

        grouped.setdefault(key, {
            "location_group": sec.get("location_group"),
            "size": sec.get("size"),
            "quantity": 0,
            "locations": []
        })

        grouped[key]["quantity"] += 1
        grouped[key]["locations"].append(
            sec.get("location")
        )

    parsed["shave_removal_aggregated"] = (
        list(grouped.values())
    )

    logger.info(
        f"🪒 Aggregated shave removals: "
        f"{parsed['shave_removal_aggregated']}"
    )

    return parsed


# =========================================================
# 🔴 CHEMICAL PEEL AGGREGATION
# =========================================================
def aggregate_chemical_peels(parsed):

    logger.info("🔧 Aggregating chemical peels...")

    grouped = {}

    for sec in parsed.get(
        "chemical_peel_sections",
        []
    ):

        key = (
            f"{sec.get('type')}_"
            f"{sec.get('method')}_"
            f"{sec.get('location')}"
        )

        grouped.setdefault(key, {
            "type": sec.get("type"),
            "method": sec.get("method"),
            "location": sec.get("location"),
            "quantity": 0,
            "sections": []
        })

        grouped[key]["quantity"] += int(
            sec.get("quantity") or 1
        )

        grouped[key]["sections"].append(sec)

    parsed["chemical_peel_aggregated"] = (
        list(grouped.values())
    )

    logger.info(
        f"📊 Aggregated chemical peels="
        f"{len(parsed['chemical_peel_aggregated'])}"
    )

    return parsed