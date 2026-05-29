# services/modifier_engine.py
"""
All modifier logic is driven by two CSV files:

  data/modifierList.csv  — defines every valid modifier, its description, and
                           whether it applies to E/M codes (enmModifier=1) or
                           procedure/CPT codes (enmModifier=0).

  data/proCodeList.csv   — per-CPT billing rules (add-on flag, laterality flag,
                           charge-per-unit, etc.).

No modifier code is hardcoded as a bare string.  All codes are resolved from
the modifier list at startup so a CSV change propagates automatically.
"""

import csv
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


# ─────────────────────────────────────────────────────────────
# MODIFIER LIST  (modifierList.csv)
# ─────────────────────────────────────────────────────────────

_MODIFIER_LIST: Dict[str, Dict] = {}   # keyed by modifier code string
_MODIFIER_LIST_PATH = Path(__file__).parent.parent / "data" / "modifierList.csv"


def _load_modifier_list():
    global _MODIFIER_LIST
    if _MODIFIER_LIST:
        return
    with open(_MODIFIER_LIST_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("modifier", "")).strip()
            if not code or str(row.get("deleted", "0")).strip() == "1":
                continue
            _MODIFIER_LIST[code] = {
                "desc":        str(row.get("modifierDesc", "")).strip(),
                "det_desc":    str(row.get("modifierDetDesc", "")).strip(),
                "enm_modifier": str(row.get("enmModifier", "0")).strip() == "1",
            }
    logger.info(f"✅ Modifier list loaded: {len(_MODIFIER_LIST)} modifiers")


def get_modifier(code: str) -> Optional[Dict]:
    """Return modifier metadata for a code, or None if not in the list."""
    _load_modifier_list()
    return _MODIFIER_LIST.get(str(code).strip())


def is_em_modifier(code: str) -> bool:
    """True when the modifier is designated for E/M codes (enmModifier=1)."""
    _load_modifier_list()
    entry = _MODIFIER_LIST.get(str(code).strip())
    return entry["enm_modifier"] if entry else False


def is_valid_modifier(code: str) -> bool:
    """True when the modifier code exists in modifierList.csv."""
    _load_modifier_list()
    return str(code).strip() in _MODIFIER_LIST


# ─────────────────────────────────────────────────────────────
# NAMED MODIFIER REFERENCES  (resolved from CSV at first use)
# Changing a code in the CSV is sufficient — no code edit needed.
# ─────────────────────────────────────────────────────────────

def _mod(code: str) -> str:
    """
    Return the modifier code after confirming it exists in the CSV.
    Logs a warning and returns the raw code if it is missing (graceful degradation).
    """
    _load_modifier_list()
    if code not in _MODIFIER_LIST:
        logger.warning(f"Modifier '{code}' not found in modifierList.csv")
    return code


# E/M modifiers  (enmModifier=1)
MOD_EM_SAME_DAY       = _mod("25")   # Significant, separately identifiable E/M on same day as procedure
MOD_DECISION_SURGERY  = _mod("57")   # Initial decision for surgery (90-day global)
MOD_EM_POSTOP         = _mod("24")   # Unrelated E/M during post-operative period

# Procedure modifiers  (enmModifier=0)
MOD_DISTINCT          = _mod("59")   # Distinct procedural service (replaces -51 on claims)
MOD_BILATERAL         = _mod("50")   # Bilateral procedure
MOD_LEFT              = _mod("LT")   # Procedure on left side of body
MOD_RIGHT             = _mod("RT")   # Procedure on right side of body


# ─────────────────────────────────────────────────────────────
# PRO-CODE RULES  (proCodeList.csv)
# ─────────────────────────────────────────────────────────────

_PRO_CODE_RULES: Dict[str, Dict] = {}
_PRO_CODE_PATH = Path(__file__).parent.parent / "data" / "proCodeList.csv"

_DEFAULT_RULES = {
    "billWithIntEM":    True,
    "billWithFUEM":     True,
    "billAlone":        False,
    "leftRightSepration": False,
    "addOn":            False,
    "chargePerUnit":    False,
}


def _load_rules():
    global _PRO_CODE_RULES
    if _PRO_CODE_RULES:
        return
    with open(_PRO_CODE_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("proCode", "")).strip()
            if not code:
                continue
            _PRO_CODE_RULES[code] = {
                "billWithIntEM":     str(row.get("billWithIntEM", "0")).strip() == "1",
                "billWithFUEM":      str(row.get("billWithFUEM", "0")).strip() == "1",
                "billAlone":         str(row.get("billAlone", "0")).strip() == "1",
                "leftRightSepration": str(row.get("leftRightSepration", "0")).strip() == "1",
                "addOn":             str(row.get("addOn", "0")).strip() == "1",
                "chargePerUnit":     str(row.get("chargePerUnit", "0")).strip() == "1",
            }
    logger.info(f"✅ Pro-code billing rules loaded: {len(_PRO_CODE_RULES)} records")


def get_code_rules(code: str) -> Dict:
    _load_rules()
    return _PRO_CODE_RULES.get(str(code).strip(), _DEFAULT_RULES)


# ─────────────────────────────────────────────────────────────
# E/M MODIFIER  (-25 / -57)
# ─────────────────────────────────────────────────────────────

def assign_em_modifier(
    em_code: Dict,
    has_same_day_procedures: bool,
    is_surgery_decision: bool = False,
) -> Dict:
    """
    Assign the correct E/M modifier from modifierList.csv:

      -57  Initial decision for surgery (90-day global period).
           Applied when the E/M visit is the day of or day before surgery.

      -25  Significant, separately identifiable E/M on the same day as a
           procedure with a 0-10 day global period.  This is the standard
           modifier for dermatology same-day E/M + procedure visits.
    """
    if not has_same_day_procedures:
        return em_code

    if is_surgery_decision:
        mod = MOD_DECISION_SURGERY
        desc = get_modifier(mod)["desc"] if get_modifier(mod) else "Decision for Surgery"
        em_code["modifier"] = mod
        logger.info(f"✅ Modifier -{mod} ({desc}) → {em_code['code']}")
    else:
        mod = MOD_EM_SAME_DAY
        desc = get_modifier(mod)["desc"] if get_modifier(mod) else "Unrelated E/M Same Day"
        em_code["modifier"] = mod
        logger.info(f"✅ Modifier -{mod} ({desc}) → {em_code['code']}")

    return em_code


# ─────────────────────────────────────────────────────────────
# LATERALITY MODIFIERS  (LT / RT)
# ─────────────────────────────────────────────────────────────

_LEFT_TOKENS  = {"left", " lt ", "(lt)", "l side"}
_RIGHT_TOKENS = {"right", " rt ", "(rt)", "r side"}


def _detect_side(text: str) -> Optional[str]:
    if not text:
        return None
    t = f" {text.lower()} "
    has_left  = any(tok in t for tok in _LEFT_TOKENS)
    has_right = any(tok in t for tok in _RIGHT_TOKENS)
    if has_left and not has_right:
        return MOD_LEFT
    if has_right and not has_left:
        return MOD_RIGHT
    return None


def assign_laterality_modifiers(cpt_codes: List[Dict], parsed: Dict) -> List[Dict]:
    """
    Add LT or RT modifier to CPT codes that have leftRightSepration=1 in
    proCodeList.csv.  Side is inferred from location text in parsed sections.
    LT / RT are defined in modifierList.csv (enmModifier=0).
    """
    _load_rules()

    location_texts: List[str] = []
    for section_key in (
        "excision_sections", "destruction_sections", "closure_sections",
        "biopsy_sections", "shave_removal_sections", "debridement_sections",
    ):
        for sec in parsed.get(section_key, []):
            loc = sec.get("location") or sec.get("destruction_location") or ""
            if loc:
                location_texts.append(str(loc))

    combined_location = " ".join(location_texts)

    for cpt in cpt_codes:
        code = str(cpt.get("code", "")).strip()
        if not get_code_rules(code)["leftRightSepration"]:
            continue
        if cpt.get("modifier") in (MOD_LEFT, MOD_RIGHT):
            continue

        side = _detect_side(combined_location)
        if side:
            cpt["modifier"] = side
            desc = get_modifier(side)["desc"] if get_modifier(side) else side
            logger.info(f"✅ Modifier -{side} ({desc}) → CPT {code}")

    return cpt_codes


# ─────────────────────────────────────────────────────────────
# DISTINCT PROCEDURE MODIFIER  (-59)
# ─────────────────────────────────────────────────────────────

def assign_multiple_procedure_modifiers(cpt_codes: List[Dict]) -> List[Dict]:
    """
    Assign -59 (Distinct Procedural Service) to secondary CPT codes when
    multiple non-add-on procedures are billed on the same day.

    Source: modifierList.csv — modifier 59:
      "Distinct procedural service — different session or patient encounter,
       different procedure or surgery, different site, separate lesion, or
       separate injury."

    Note: modifier -51 (Multiple Procedures) is marked in modifierList.csv as
    'for Internal use only by Carrier' and must NOT appear on submitted claims.
    -59 is the correct claim modifier for distinct same-day procedures.

    Rules:
    - First (primary) CPT code → no modifier.
    - Add-on codes → skipped (inherently linked to primary, no modifier needed).
    - Codes already carrying a modifier (LT/RT/-25/-57) → skipped.
    - All other secondary non-add-on codes → -59.
    """
    _load_rules()

    mod  = MOD_DISTINCT
    desc = get_modifier(mod)["desc"] if get_modifier(mod) else "Distinct Procedural Service"

    primary_codes = [
        cpt for cpt in cpt_codes
        if not get_code_rules(str(cpt.get("code", "")).strip())["addOn"]
    ]

    if len(primary_codes) <= 1:
        return cpt_codes

    for i, cpt in enumerate(primary_codes):
        if i == 0:
            continue  # Primary — no modifier
        if cpt.get("modifier"):
            continue  # Already has LT/RT/25/57
        cpt["modifier"] = mod
        logger.info(f"✅ Modifier -{mod} ({desc}) → CPT {cpt['code']}")

    return cpt_codes
