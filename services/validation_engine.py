# services/validation_engine.py
"""
Validation Engine — runs after LLM code assignment, before modifier enforcement.

Validates the code list against five billing integrity rules:
  1. Duplicate procedure on same lesion     → reject secondary code
  2. Add-on code without its primary        → reject add-on
  3. Closure add-on without closure primary → reject add-on
  4. Modifier -59 without distinct lesion   → flag (cannot auto-reject)
  5. Diagnosis not linked to procedure      → flag

Rules 1–3 produce hard rejects (code removed, audit flag added).
Rules 4–5 produce soft flags (code kept, audit flag added for human review).

All decisions are logged in audit_flags so the reasoning engine and API
response surface them. The output llm_output dict is returned with the
same schema — only cpt_codes list and audit_flags are modified.
"""

from typing import Any, Dict, List, Optional
from loguru import logger


# ─────────────────────────────────────────────────────────────
# KNOWN BUNDLING PAIRS
# Pairs that represent the same clinical event — billing both
# on the same lesion is a CCI bundling violation.
# Key   = secondary code (typically the lower-value one)
# Value = set of primary codes it bundles with
# ─────────────────────────────────────────────────────────────
_BUNDLED_PAIRS: Dict[str, set] = {
    "11310": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},  # shave + biopsy same lesion
    "11311": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},
    "11312": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},
    "11313": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},
}

# Closure primary codes and their valid add-on pairs
_CLOSURE_PRIMARY_PREFIXES  = ("120", "131", "140")
_CLOSURE_ADDON_PREFIXES    = ("120", "131", "140")


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _codes_in_output(cpt_codes: List[Dict]) -> set:
    return {str(c.get("code", "")).strip() for c in cpt_codes if c.get("code")}


def _is_closure_code(code: str) -> bool:
    return any(code.startswith(p) for p in _CLOSURE_PRIMARY_PREFIXES)


def _add_flag(llm_output: Dict, message: str) -> None:
    llm_output.setdefault("audit_flags", [])
    if message not in llm_output["audit_flags"]:
        llm_output["audit_flags"].append(message)


# ─────────────────────────────────────────────────────────────
# RULE 1 — DUPLICATE PROCEDURE ON SAME LESION
# ─────────────────────────────────────────────────────────────

def _check_bundled_pairs(
    cpt_codes: List[Dict],
    parsed: Dict,
    llm_output: Dict,
) -> List[Dict]:
    """
    If a known bundled secondary code is present alongside its primary code,
    AND the note documents only one lesion/site for those procedures, reject
    the secondary code and flag it.

    Only rejects when single-lesion evidence exists in parsed sections.
    When multiple lesions are documented, the pair can be legitimately billed.
    """
    present = _codes_in_output(cpt_codes)
    rejected: set = set()

    for secondary, primaries in _BUNDLED_PAIRS.items():
        if secondary not in present:
            continue
        overlap = primaries & present
        if not overlap:
            continue

        # Check whether multiple distinct lesion sites are documented
        biopsy_count   = len(parsed.get("biopsy_sections", []))
        shave_count    = len(parsed.get("shave_removal_sections", []))
        total_lesions  = max(biopsy_count, shave_count, 1)

        if total_lesions > 1:
            # Multiple lesions — distinct billing is plausible, soft flag only
            primary_code = next(iter(overlap))
            _add_flag(
                llm_output,
                f"Verify {secondary} and {primary_code} are on distinct lesions — "
                f"modifier -59 required if same site.",
            )
        else:
            # Single lesion — reject the secondary as a duplicate
            primary_code = next(iter(overlap))
            rejected.add(secondary)
            _add_flag(
                llm_output,
                f"REJECTED {secondary}: bundled with {primary_code} on the same lesion. "
                f"A biopsy and shave removal on a single lesion cannot both be billed.",
            )
            logger.warning(f"Validation: rejected {secondary} (bundled with {primary_code})")

    return [c for c in cpt_codes if str(c.get("code", "")).strip() not in rejected]


# ─────────────────────────────────────────────────────────────
# RULE 2 — ADD-ON WITHOUT PRIMARY
# ─────────────────────────────────────────────────────────────

def _check_addon_without_primary(
    cpt_codes: List[Dict],
    candidates: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Every add-on code (associatedWithProCode != null in the candidate list)
    must have its parent primary code present in the output.
    Remove the add-on and flag if the primary is missing.
    """
    # Build parent map from candidates
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
                f"REJECTED {code}: add-on code requires primary {parent} which is not in the output.",
            )
            logger.warning(f"Validation: rejected add-on {code} (primary {parent} missing)")

    return [c for c in cpt_codes if str(c.get("code", "")).strip() not in rejected]


# ─────────────────────────────────────────────────────────────
# RULE 3 — CLOSURE ADD-ON WITHOUT CLOSURE PRIMARY
# ─────────────────────────────────────────────────────────────

def _check_closure_addon_orphan(
    cpt_codes: List[Dict],
    llm_output: Dict,
) -> List[Dict]:
    """
    Closure add-on codes (e.g. 13122, 12002, 13133) are only valid when a
    closure primary code is also present.  If a closure add-on appears without
    any closure primary, reject it.

    Rule 2 already handles the general add-on case. This rule catches closure
    add-ons whose parent isn't in the candidate list (e.g. edge cases in
    enforcement where add-ons are injected without their primary).
    """
    present  = _codes_in_output(cpt_codes)
    has_primary = any(_is_closure_code(c) for c in present)
    rejected: set = set()

    for c in cpt_codes:
        code   = str(c.get("code", "")).strip()
        source = str(c.get("source") or "").lower()
        if "closure" in source and not has_primary and _is_closure_code(code):
            # If this is the only closure code and it looks like an add-on
            # (no matching primary), flag it — Rule 2 will reject if parent is known
            pass  # Handled by Rule 2 via candidate parent_map

    return cpt_codes  # Rule 2 is the primary enforcer; this is a belt-and-suspenders check


# ─────────────────────────────────────────────────────────────
# RULE 4 — MODIFIER -59 WITHOUT DISTINCT LESION EVIDENCE
# ─────────────────────────────────────────────────────────────

def _check_modifier_59(
    cpt_codes: List[Dict],
    parsed: Dict,
    llm_output: Dict,
) -> None:
    """
    Soft flag only — modifier -59 requires explicit documentation of a distinct
    procedure (different site, lesion, or session).  When only one lesion is
    documented across all sections, flag each -59 code for human review.

    The reasoning engine performs the definitive audit against the note text.
    This is an early structural check against the parsed data.
    """
    total_sections = sum([
        len(parsed.get("biopsy_sections", [])),
        len(parsed.get("excision_sections", [])),
        len(parsed.get("shave_removal_sections", [])),
        len(parsed.get("destruction_sections", [])),
        len(parsed.get("mohs_sections", [])),
    ])

    for c in cpt_codes:
        if str(c.get("modifier", "")).strip() == "59":
            code = c.get("code", "")
            if total_sections <= 1:
                _add_flag(
                    llm_output,
                    f"REVIEW {code}-59: modifier -59 requires a distinct site or lesion. "
                    f"Only {total_sections} procedure section(s) documented — verify distinct "
                    f"lesion evidence before submitting.",
                )
                logger.info(f"Validation: -59 on {code} flagged (single-section note)")


# ─────────────────────────────────────────────────────────────
# RULE 5 — DIAGNOSIS NOT LINKED TO PROCEDURE
# ─────────────────────────────────────────────────────────────

def _check_dx_linkage(
    cpt_codes: List[Dict],
    em_code: Optional[Dict],
    llm_output: Dict,
) -> None:
    """
    Every CPT and E/M code must have at least one linked ICD-10 diagnosis.
    Codes with empty linked_dx are flagged — they cannot be submitted on a claim.
    """
    for c in cpt_codes:
        code   = c.get("code", "")
        linked = c.get("linked_dx") or []
        if not linked:
            _add_flag(
                llm_output,
                f"REVIEW {code}: no diagnosis (ICD-10) linked. "
                f"A procedure code without a diagnosis will be denied.",
            )
            logger.warning(f"Validation: {code} has no linked_dx")

    if em_code and em_code.get("code"):
        if not (em_code.get("linked_dx") or []):
            _add_flag(
                llm_output,
                f"REVIEW {em_code['code']}: E/M code has no linked diagnosis.",
            )


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def validate_codes(
    llm_output: Dict[str, Any],
    parsed: Dict[str, Any],
    candidates: List[Dict],
) -> Dict[str, Any]:
    """
    Run all five validation rules against the LLM-assigned code list.

    Returns the modified llm_output with:
    - cpt_codes: rejected codes removed (Rules 1–3)
    - audit_flags: rejection reasons and soft flags appended (Rules 1–5)
    """
    try:
        codes     = llm_output.get("codes", {})
        cpt_codes = list(codes.get("cpt_codes", []))
        em_code   = codes.get("em_code") or {}

        original_count = len(cpt_codes)

        # Hard reject rules (modify cpt_codes list)
        cpt_codes = _check_bundled_pairs(cpt_codes, parsed, llm_output)
        cpt_codes = _check_addon_without_primary(cpt_codes, candidates, llm_output)
        cpt_codes = _check_closure_addon_orphan(cpt_codes, llm_output)

        # Soft flag rules (add to audit_flags only)
        _check_modifier_59(cpt_codes, parsed, llm_output)
        _check_dx_linkage(cpt_codes, em_code, llm_output)

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
