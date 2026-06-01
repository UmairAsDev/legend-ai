# services/validation_engine.py
"""
Billing Integrity Validation Engine.

Runs after LLM code assignment, before modifier enforcement.

All rules are PROCEDURE-AGNOSTIC — they work from the data already attached
to each code (minQty, maxQty, minSize, maxSize, associatedWithProCode) rather
than hardcoding specific CPT numbers or procedure types.

Rules:
  1. Add-on code without its primary (hard reject)
  2. Quantity out of the code's documented range from proCodeList (hard reject)
  3. Duplicate: same CPT + same DX + same location billed more than once (hard reject)
  4. Modifier -59 applied to a procedure with no distinct site evidence (soft flag)
  5. CPT code with no linked ICD-10 diagnosis (soft flag)
  6. NCCI bundled pairs: secondary code bundled with a co-billed primary (hard reject unless -59)
"""

from typing import Any, Dict, List, Optional
from loguru import logger

from config.constants import CLOSURE_CODE_PREFIXES
from services.knowledge_base import kb
from services.lesion_validator import validate_lesion_conflicts


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _codes_in_output(cpt_codes: List[Dict]) -> set:
    return {str(c.get("code", "")).strip() for c in cpt_codes if c.get("code")}


def _is_closure_code(code: str) -> bool:
    return any(code.startswith(p) for p in CLOSURE_CODE_PREFIXES)


def _add_flag(llm_output: Dict, message: str) -> None:
    llm_output.setdefault("audit_flags", [])
    if message not in llm_output["audit_flags"]:
        llm_output["audit_flags"].append(message)


# ─────────────────────────────────────────────────────────────
# RULE 1 — ADD-ON WITHOUT ITS PRIMARY
# Procedure-agnostic: uses associatedWithProCode from CSV data.
# ─────────────────────────────────────────────────────────────

def _check_addon_without_primary(
    cpt_codes: List[Dict],
    candidates: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Every add-on code must have its parent primary code in the output.
    The parent is identified by associatedWithProCode — no code numbers hardcoded.
    Applies to closure add-ons, biopsy add-ons, destruction add-ons, and any other
    add-on defined in proCodeList.csv.
    """
    parent_map: Dict[str, str] = {}
    for c in candidates:
        addon  = str(c.get("code", "")).strip()
        parent = str(c.get("associatedWithProCode") or "").strip()
        if addon and parent and parent not in ("", "0", "None", "null"):
            parent_map[addon] = parent

    present  = _codes_in_output(cpt_codes)
    rejected: set = set()

    for code, parent in parent_map.items():
        if code in present and parent not in present:
            rejected.add(code)
            _add_flag(
                llm_output,
                f"REJECTED {code}: add-on requires primary {parent} which is not in the output.",
            )
            logger.warning(f"Validation rule 1: rejected add-on {code} (primary {parent} missing)")

    return [c for c in cpt_codes if str(c.get("code", "")).strip() not in rejected]


# ─────────────────────────────────────────────────────────────
# RULE 2 — QUANTITY OUT OF RANGE
# Procedure-agnostic: uses minQty/maxQty from CSV data.
# ─────────────────────────────────────────────────────────────

def _check_quantity_ranges(
    cpt_codes: List[Dict],
    candidates: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Each code's quantity must fall within the [minQty, maxQty] range documented
    in proCodeList.csv.  Codes assigned with an out-of-range quantity are rejected.
    Applies to ALL procedure types — nail debridement, destruction, biopsy, etc.
    """
    candidate_map: Dict[str, Dict] = {
        str(c.get("code", "")).strip(): c
        for c in candidates if c.get("code")
    }

    rejected: set = set()

    for cpt in cpt_codes:
        code = str(cpt.get("code", "")).strip()
        candidate = candidate_map.get(code)
        if not candidate:
            continue

        min_q = candidate.get("minQty") or 0
        max_q = candidate.get("maxQty")
        if max_q is None:
            continue  # no range defined — skip

        try:
            qty = int(float(cpt.get("quantity") or 1))
        except (ValueError, TypeError):
            continue

        if not (min_q <= qty <= max_q):
            rejected.add(code)
            _add_flag(
                llm_output,
                f"REJECTED {code}: quantity {qty} is outside the valid range "
                f"[{min_q}–{max_q}] for this code.",
            )
            logger.warning(f"Validation rule 2: rejected {code} qty={qty} range=[{min_q},{max_q}]")

    return [c for c in cpt_codes if str(c.get("code", "")).strip() not in rejected]


# ─────────────────────────────────────────────────────────────
# RULE 3 — DUPLICATE: SAME CODE + DX + LOCATION
# Procedure-agnostic: uses the grouping key (CPT + DX + location).
# ─────────────────────────────────────────────────────────────

def _check_duplicates(
    cpt_codes: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Same CPT + same ICD-10 code(s) + same location may not appear more than once.
    This enforces the core grouping rule that should have been applied by the LLM
    coder but occasionally is violated on complex multi-procedure notes.
    """
    seen: Dict[tuple, Dict] = {}
    rejected: set = set()

    for cpt in cpt_codes:
        code     = str(cpt.get("code", "")).strip()
        dx_key   = tuple(sorted(cpt.get("linked_dx") or []))
        loc_key  = str(cpt.get("location") or "")  # may be absent on simple codes
        group_key = (code, dx_key, loc_key)

        if group_key in seen:
            rejected.add(id(cpt))
            existing = seen[group_key]
            _add_flag(
                llm_output,
                f"REJECTED duplicate {code}: same code, DX {list(dx_key)!r}, "
                f"and location '{loc_key}' appears more than once. "
                f"Merge quantities into a single entry.",
            )
            logger.warning(f"Validation rule 3: duplicate {code} {group_key}")
        else:
            seen[group_key] = cpt

    return [c for c in cpt_codes if id(c) not in rejected]


# ─────────────────────────────────────────────────────────────
# RULE 4 — MODIFIER -59 WITHOUT DISTINCT SITE EVIDENCE
# ─────────────────────────────────────────────────────────────

def _check_modifier_59(
    cpt_codes: List[Dict],
    parsed: Dict,
    llm_output: Dict,
) -> None:
    """
    Modifier -59 requires explicit documentation of a distinct procedure —
    different anatomical site, lesion, or session.

    Flags any -59 code when the total number of documented procedure sections
    is ≤ 1, since a single-section note cannot support a distinct service claim.

    This is a structural pre-check. The reasoning engine performs the definitive
    audit against the note text. Codes are not rejected here — only flagged.
    """
    # Count all documented procedure sections across all types
    section_keys = [
        "biopsy_sections", "excision_sections", "shave_removal_sections",
        "destruction_sections", "mohs_sections", "closure_sections",
        "srt_sections", "debridement_sections", "xtrac_sections",
        "ipl_sections", "laser_treatment_sections", "filler_sections",
        "filler_material_sections", "chemical_peel_sections",
    ]
    total_sections = sum(len(parsed.get(k, [])) for k in section_keys)

    for cpt in cpt_codes:
        if str(cpt.get("modifier", "")).strip() != "59":
            continue
        code = cpt.get("code", "")
        if total_sections <= 1:
            _add_flag(
                llm_output,
                f"REVIEW {code}-59: modifier -59 requires a distinct site or lesion. "
                f"Only {total_sections} procedure section(s) documented. "
                f"Verify distinct documentation before submitting.",
            )
            logger.info(f"Validation rule 4: -59 on {code} flagged (total_sections={total_sections})")


# ─────────────────────────────────────────────────────────────
# RULE 5 — DX NOT LINKED TO PROCEDURE
# ─────────────────────────────────────────────────────────────

def _check_dx_linkage(
    cpt_codes: List[Dict],
    em_code: Optional[Dict],
    llm_output: Dict,
) -> None:
    """
    Every CPT and E/M code must have at least one linked ICD-10 diagnosis.
    Codes with empty linked_dx will be denied by any payer.
    """
    for cpt in cpt_codes:
        code   = cpt.get("code", "")
        linked = cpt.get("linked_dx") or []
        if not linked:
            _add_flag(
                llm_output,
                f"REVIEW {code}: no diagnosis (ICD-10) linked. "
                f"A procedure code without a diagnosis will be denied.",
            )
            logger.warning(f"Validation rule 5: {code} has no linked_dx")

    if em_code and em_code.get("code") and not (em_code.get("linked_dx") or []):
        _add_flag(
            llm_output,
            f"REVIEW {em_code['code']}: E/M code has no linked diagnosis.",
        )


# ─────────────────────────────────────────────────────────────
# RULE 0 — HALLUCINATED CODES
# ─────────────────────────────────────────────────────────────

def _check_hallucinated_codes(
    cpt_codes: List[Dict],
    candidates: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Reject any CPT code not present in the retrieved candidates list.

    The retriever fetches codes from proCodeList.csv filtered to this note's
    detected procedure families.  A code the LLM assigns that was never
    retrieved has no knowledge-base backing — it is hallucinated.

    Confirmed selector codes (confidence='confirmed') are exempt because
    they come directly from the CSV via deterministic selectors.
    """
    known_codes: set = {
        str(c.get("code", "")).strip()
        for c in candidates
        if c.get("code")
    }

    rejected: set = set()

    for cpt in cpt_codes:
        code = str(cpt.get("code", "")).strip()
        if not code:
            continue
        if cpt.get("confidence") == "confirmed":
            continue   # selector-confirmed — always valid
        if code not in known_codes:
            rejected.add(code)
            _add_flag(
                llm_output,
                f"REJECTED {code}: not in retrieved candidate list — "
                f"not a supported procedure for this note.",
            )
            logger.warning(f"Hallucination guard: rejected {code}")

    return [c for c in cpt_codes if str(c.get("code", "")).strip() not in rejected]


# ─────────────────────────────────────────────────────────────
# RULE 6 — PROCEDURE-SPECIFIC SITE RULES (SOFT FLAGS)
# Uses proName from the KnowledgeBase — no hardcoded CPT numbers.
# ─────────────────────────────────────────────────────────────

def _check_procedure_site_rules(
    cpt_codes: List[Dict],
    parsed: Dict,
    llm_output: Dict,
) -> List[Dict]:
    """
    Procedure-specific validation rules based on proName from the KnowledgeBase.
    All rules are soft flags (audit_flags) — no hard rejections here.
    """
    try:
        mohs_sections = parsed.get("mohs_sections", [])
        total_mohs_stages = sum(int(s.get("stages") or 1) for s in mohs_sections)

        for cpt in cpt_codes:
            code = str(cpt.get("code", "")).strip()
            cpt_meta = kb.get_cpt(code)
            if not cpt_meta:
                continue
            pro_name = cpt_meta.pro_name

            # ── Rule A: Mohs additional-stage code requires stages > 1 ─────────
            if (
                pro_name == "MOHS Micrographic Surgery"
                and cpt_meta.parent_code is not None
            ):
                if total_mohs_stages <= 1:
                    _add_flag(
                        llm_output,
                        f"REVIEW {code}: Mohs additional-stage code requires "
                        f"documented stages > 1 but note shows {total_mohs_stages} stage(s).",
                    )

            # ── Rule B: Malignant excision requires a malignant diagnosis ───────
            if pro_name == "Excision Malignant Lesion & Margins":
                linked = [str(d).strip().upper() for d in (cpt.get("linked_dx") or [])]
                # Malignant diagnoses: C00-C49 melanoma/carcinoma range, C43.x, C44.x
                has_malignant_dx = any(
                    d.startswith(("C43", "C44", "C00", "C01", "C02", "C03", "C04",
                                  "C05", "C06", "C07", "C08", "C09", "C10", "C11",
                                  "C14", "C15", "C16", "C17", "C18", "C19", "C20",
                                  "C21", "C22", "C30", "C31", "C32", "C33", "C34",
                                  "C40", "C41", "C43", "C44", "C45", "C46", "C47",
                                  "C48", "C49"))
                    for d in linked
                )
                if not has_malignant_dx:
                    _add_flag(
                        llm_output,
                        f"REVIEW {code}: malignant excision code but no malignant "
                        f"diagnosis (C43/C44 range) is linked. Verify diagnosis assignment.",
                    )

            # ── Rule C: Closure code must match documented closure type ─────────
            if pro_name in ("Simple Closure", "Layered Closure", "Complex Closure"):
                closure_sections = parsed.get("closure_sections", [])
                doc_types = {(s.get("type") or "").lower() for s in closure_sections}
                code_type_map = {
                    "Simple Closure":    {"simple"},
                    "Layered Closure":   {"intermediate", "layered"},
                    "Complex Closure":   {"complex"},
                }
                expected = code_type_map.get(pro_name, set())
                if doc_types and not (doc_types & expected):
                    _add_flag(
                        llm_output,
                        f"REVIEW {code} ({pro_name}): code type does not match "
                        f"documented closure type(s) {doc_types}. Verify closure documentation.",
                    )

            # ── Rule D: ATT code location must match note ───────────────────────
            if pro_name == "Adjacent Tissue Transfer":
                desc_lower = cpt_meta.description.lower()
                site_id = cpt.get("site_id", "")
                # If code description says "trunk" but site is neck — flag it
                if "trunk" in desc_lower:
                    mohs_secs = parsed.get("mohs_sections", [])
                    has_neck_mohs = any(
                        "neck" in (s.get("location") or "").lower()
                        for s in mohs_secs
                    )
                    if has_neck_mohs:
                        _add_flag(
                            llm_output,
                            f"REVIEW {code}: ATT trunk code selected but Mohs site "
                            f"is neck — verify correct ATT location family.",
                        )

        return cpt_codes

    except Exception as e:
        logger.warning(f"_check_procedure_site_rules (non-fatal): {e}")
        return cpt_codes


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def validate_codes(
    llm_output: Dict[str, Any],
    parsed: Dict[str, Any],
    candidates: List[Dict],
) -> Dict[str, Any]:
    """
    Run all validation rules against the assigned code list.

    Hard rejects (Rules 1–3): remove the offending code, add audit flag.
    Soft flags (Rules 4–5):   keep the code, add audit flag for human review.

    Returns modified llm_output — same schema, only cpt_codes and audit_flags changed.
    """
    try:
        codes     = llm_output.get("codes", {})
        cpt_codes = list(codes.get("cpt_codes", []))
        em_code   = codes.get("em_code") or {}

        original_count = len(cpt_codes)

        # Hard reject rules
        cpt_codes = _check_hallucinated_codes(cpt_codes, candidates, llm_output)
        cpt_codes = _check_addon_without_primary(cpt_codes, candidates, llm_output)
        cpt_codes = _check_quantity_ranges(cpt_codes, candidates, llm_output)
        cpt_codes = _check_duplicates(cpt_codes, llm_output)

        # Phase 5: same-site lesion conflict validation (NCCI + dermatology rules)
        llm_output["codes"]["cpt_codes"] = cpt_codes
        llm_output = validate_lesion_conflicts(llm_output, candidates)
        cpt_codes = list(llm_output.get("codes", {}).get("cpt_codes", []))

        # Soft flag rules
        _check_modifier_59(cpt_codes, parsed, llm_output)
        _check_dx_linkage(cpt_codes, em_code, llm_output)

        # Procedure-specific rules (soft flags only)
        cpt_codes = _check_procedure_site_rules(cpt_codes, parsed, llm_output)
        llm_output["codes"]["cpt_codes"] = cpt_codes

        rejected_count = original_count - len(cpt_codes)
        if rejected_count:
            logger.info(f"Validation: {rejected_count} code(s) rejected, {len(cpt_codes)} remain")
        else:
            logger.info("Validation: all codes passed hard rules")

        llm_output["codes"]["cpt_codes"] = cpt_codes
        return llm_output

    except Exception as e:
        logger.exception(f"Validation engine failed (non-fatal): {e}")
        return llm_output
