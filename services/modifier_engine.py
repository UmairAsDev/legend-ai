# services/modifier_engine.py
"""
Phase 8 — Modifier Engine (complete rewrite).

Design rules from the plan:
  - Modifier -59: only when two non-add-on procedures are at DIFFERENT sites.
                  Never assigned by default.  Never assigned to add-on codes.
  - Add-on codes: never receive any procedure modifier.
  - LT / RT:      only when the procedure's own section explicitly documents
                  left or right.  Not assigned when location is ambiguous.
  - Modifier -25: assigned when a separately identifiable E/M is documented
                  on the same day as a procedure (already handled in assign_em_modifier).
  - Modifier -57: for the decision for surgery on the day of/before a major
                  procedure (90-day global).

All modifier codes are resolved from modifierList.csv via the KnowledgeBase.
No modifier code string is hardcoded as a bare literal.
"""

from typing import Dict, List, Optional

from loguru import logger

from services.knowledge_base import kb
from services.site_builder import ProcedureSite


# ─────────────────────────────────────────────────────────────────────────────
# NAMED MODIFIER CONSTANTS
# Resolved from modifierList.csv at import time.  If the code is missing,
# log a warning and carry the raw string (graceful degradation).
# ─────────────────────────────────────────────────────────────────────────────

def _mod(code: str) -> str:
    kb.load()
    if not kb.is_valid_modifier(code):
        logger.warning(f"Modifier '{code}' not found in modifierList.csv")
    return code


MOD_25 = _mod("25")   # Significant, separately identifiable E/M — same day as procedure
MOD_57 = _mod("57")   # Initial decision for surgery (90-day global period)
MOD_24 = _mod("24")   # Unrelated E/M during post-operative period
MOD_59 = _mod("59")   # Distinct procedural service — different site / lesion / incision
MOD_LT = _mod("LT")   # Left side
MOD_RT = _mod("RT")   # Right side


# ─────────────────────────────────────────────────────────────────────────────
# LATERALITY DETECTION
# ─────────────────────────────────────────────────────────────────────────────

_LEFT_TOKENS  = {"left", " lt ", "(lt)", "l side"}
_RIGHT_TOKENS = {"right", " rt ", "(rt)", "r side"}

# Maps a CPT source tag → (parsed section key, location field).
# Used to scope laterality detection to the procedure that produced the code.
_SOURCE_SECTION_MAP: Dict[str, tuple] = {
    "excision":          ("excision_sections",       "location"),
    "biopsy":            ("biopsy_sections",          "location"),
    "destruction_db":    ("destruction_sections",     "destruction_location"),
    "destruction_dpm":   ("destruction_sections",     "destruction_location"),
    "destruction_dm":    ("destruction_sections",     "destruction_location"),
    "mohs":              ("mohs_sections",            "location"),
    "shave_removal":     ("shave_removal_sections",   "location"),
    "closure":           ("closure_sections",         "location"),
    "debridement":       ("debridement_sections",     "location"),
    "srt":               ("srt_sections",             "location"),
}


def _detect_side(text: str) -> Optional[str]:
    """
    Return MOD_LT, MOD_RT, or None.
    Returns None when both sides appear (ambiguous) or text is empty.
    """
    if not text:
        return None
    t = f" {text.lower()} "
    has_left  = any(tok in t for tok in _LEFT_TOKENS)
    has_right = any(tok in t for tok in _RIGHT_TOKENS)
    if has_left and not has_right:
        return MOD_LT
    if has_right and not has_left:
        return MOD_RT
    return None


def _location_for_code(code_dict: Dict, parsed: Dict) -> str:
    """
    Return the location text for the sections that produced this code,
    scoped to the procedure type so that mixed-side notes don't cancel out.
    """
    source = code_dict.get("source", "")
    mapping = _SOURCE_SECTION_MAP.get(source)
    if not mapping:
        return ""
    section_key, loc_field = mapping
    return " ".join(
        str(sec.get(loc_field) or "")
        for sec in parsed.get(section_key, [])
        if sec.get(loc_field)
    )


# ─────────────────────────────────────────────────────────────────────────────
# E/M MODIFIER ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

def assign_em_modifier(
    em_code: Dict,
    has_same_day_procedures: bool,
    is_surgery_decision: bool = False,
) -> Dict:
    """
    Assign the correct E/M modifier:

      -57  Initial decision for surgery (90-day global period).
           Used when the E/M visit occurs on the day of or before surgery.

      -25  Significant, separately identifiable E/M on the same day as a
           procedure with a 0-10 day global period.  This is the standard
           modifier for dermatology same-day E/M + procedure visits.

    Modifier is only assigned when there are same-day procedures.
    """
    if not has_same_day_procedures:
        return em_code

    mod = MOD_57 if is_surgery_decision else MOD_25
    em_code["modifier"] = mod

    mod_info = kb.get_modifier(mod)
    desc = mod_info.description if mod_info else ""
    logger.info(f"Modifier -{mod} ({desc}) → {em_code.get('code', '')}")
    return em_code


# ─────────────────────────────────────────────────────────────────────────────
# LATERALITY MODIFIER ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

def assign_laterality_modifiers(cpt_codes: List[Dict], parsed: Dict) -> List[Dict]:
    """
    LT/RT is not auto-assigned.
    leftRightSepration is not in the approved CSV column set.
    Laterality is applied by the billing team as needed.
    """
    return cpt_codes


# ─────────────────────────────────────────────────────────────────────────────
# ADD-ON DETECTION HELPER
# Uses addOn flag AND associatedWithProCode — both sources are checked because
# some codes have a parent (associatedWithProCode set) but addOn=0 in the CSV
# (e.g., Mohs additional-stage codes 17312/17314).
# ─────────────────────────────────────────────────────────────────────────────

def _is_addon(cpt: Dict) -> bool:
    """True if this code is an add-on by either KB flag or parent code link."""
    code = str(cpt.get("code", "")).strip()
    return kb.is_addon(code)   # kb.is_addon checks both _addon_flag and parent_code


# ─────────────────────────────────────────────────────────────────────────────
# DISTINCT PROCEDURE MODIFIER (-59) — SITE-AWARE
# ─────────────────────────────────────────────────────────────────────────────

def assign_distinct_procedure_modifiers(
    cpt_codes: List[Dict],
    sites: List[Dict],
) -> List[Dict]:
    """
    Assign modifier -59 (Distinct Procedural Service) only to non-add-on
    CPT codes that are at a DIFFERENT site from the primary procedure.

    Rules:
      1. Add-on codes never receive modifiers.
      2. The primary (lowest-code-number non-add-on) gets no modifier.
      3. Secondary non-add-on codes at a different site → -59.
      4. Secondary non-add-on codes at the SAME site as the primary are NOT
         given -59 here; same-site conflicts are handled by the lesion
         validator (Phase 5).
      5. When site_id is absent on a code (e.g. LLM-assigned code without
         a section origin), -59 is not assigned — the case is flagged for
         human review.

    This replaces the old "multiple procedures = -59" blanket logic.
    """
    non_addons = [
        cpt for cpt in cpt_codes
        if not _is_addon(cpt)
    ]

    if len(non_addons) <= 1:
        return cpt_codes

    # Sort by CPT code number — lowest becomes primary (deterministic, auditable)
    non_addons.sort(key=lambda c: str(c.get("code", "")).strip())

    primary      = non_addons[0]
    primary_site = primary.get("site_id", "")

    mod_info = kb.get_modifier(MOD_59)
    desc = mod_info.description if mod_info else "Distinct Procedural Service"

    for cpt in non_addons[1:]:
        if cpt.get("modifier"):
            continue   # already carries LT / RT / -25 / -57

        code_site = cpt.get("site_id", "")

        if not primary_site or not code_site:
            logger.warning(
                f"Cannot determine site for CPT {cpt.get('code')} — "
                f"-59 not assigned.  Flag for manual review."
            )
            continue

        if code_site != primary_site:
            cpt["modifier"] = MOD_59
            logger.info(
                f"Modifier -{MOD_59} ({desc}) → CPT {cpt.get('code')} "
                f"(site {code_site} ≠ primary site {primary_site})"
            )
        else:
            logger.debug(
                f"Same-site codes: {primary.get('code')} and {cpt.get('code')} "
                f"at site {code_site} — no -59 assigned (lesion validator handles)"
            )

    return cpt_codes


# ─────────────────────────────────────────────────────────────────────────────
# BACKWARD-COMPAT ALIAS
# engine_utils.py calls assign_multiple_procedure_modifiers; route through
# the new site-unaware version so existing code keeps working until it is
# updated to pass sites.
# ─────────────────────────────────────────────────────────────────────────────

def assign_multiple_procedure_modifiers(cpt_codes: List[Dict]) -> List[Dict]:
    """
    Legacy entry point — used by engine_utils.enforce_em_and_modifiers until
    that function is updated to pass the sites list.

    Falls back to a site-unaware version: -59 is assigned to secondary codes
    with no modifier.  Prefer assign_distinct_procedure_modifiers(codes, sites)
    when sites are available.
    """
    non_addons = [cpt for cpt in cpt_codes if not _is_addon(cpt)]

    if len(non_addons) <= 1:
        return cpt_codes

    non_addons.sort(key=lambda c: str(c.get("code", "")).strip())

    mod_info = kb.get_modifier(MOD_59)
    desc = mod_info.description if mod_info else "Distinct Procedural Service"

    for i, cpt in enumerate(non_addons):
        if i == 0:
            continue
        if cpt.get("modifier"):
            continue
        cpt["modifier"] = MOD_59
        logger.info(f"Modifier -{MOD_59} ({desc}) → CPT {cpt.get('code')} (legacy path)")

    return cpt_codes


# ─────────────────────────────────────────────────────────────────────────────
# BACKWARD-COMPAT SHIMS
# ─────────────────────────────────────────────────────────────────────────────

def get_modifier(code: str):
    m = kb.get_modifier(code)
    if not m:
        return None
    return {"desc": m.description, "det_desc": m.det_description, "enm_modifier": m.is_em_modifier}


def is_em_modifier(code: str) -> bool:
    return kb.is_em_modifier(code)


def is_valid_modifier(code: str) -> bool:
    return kb.is_valid_modifier(code)


def get_code_rules(code: str) -> Dict:
    """Backward compat — returns only approved-column fields."""
    cpt = kb.get_cpt(code)
    if not cpt:
        return {"addOn": False, "chargePerUnit": False}
    return {
        "addOn":         cpt.is_addon,
        "chargePerUnit": cpt.charge_per_unit,
    }
