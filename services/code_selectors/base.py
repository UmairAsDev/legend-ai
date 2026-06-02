# services/code_selectors/base.py

import csv
import math
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger

_CSV_PATH = Path(__file__).parent.parent.parent / "data" / "proCodeList.csv"
_CACHE: Dict[str, List[dict]] = {}


# ------------------------------------------------------------------
# CSV LOADING
# ------------------------------------------------------------------

def load_codes_by_name(pro_name: str) -> List[dict]:
    """
    Load and cache all proCodeList rows whose proName matches pro_name
    (case-insensitive).  Returns dicts with numeric fields already cast.
    """
    key = pro_name.lower()
    if key in _CACHE:
        return _CACHE[key]

    rows = []
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("proName", "")).strip().lower() == key:
                parent = _normalise_assoc(row.get("associatedWithProCode"))
                addon_flag = str(row.get("addOn", "0")).strip() == "1"
                rows.append({
                    "code": str(row["proCode"]).strip(),
                    "description": str(row.get("codeDesc", "")).strip(),
                    "proName": str(row.get("proName", "")).strip(),
                    "type": "cpt",
                    "associatedWithProCode": parent,
                    "minSize": _f(row.get("minSize")),
                    "maxSize": _f(row.get("maxSize")),
                    "minQty": _i(row.get("minQty")),
                    "maxQty": _i(row.get("maxQty")),
                    "chargePerUnit": str(row.get("chargePerUnit", "0")).strip() == "1",
                    # add-on: true when addOn flag is set OR parent code is linked
                    "addOn": addon_flag or (parent is not None),
                })

    _CACHE[key] = rows
    return rows


def _normalise_assoc(val) -> Optional[str]:
    if not val:
        return None
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s not in ("", "0", "None", "null") else None


def _f(val) -> float:
    try:
        result = float(val) if val not in (None, "", "nan") else 0.0
        return 0.0 if math.isnan(result) or math.isinf(result) else result
    except (ValueError, TypeError):
        return 0.0


def _i(val) -> int:
    try:
        return int(float(val)) if val not in (None, "", "nan") else 1
    except (ValueError, TypeError):
        return 1


# ------------------------------------------------------------------
# LOCATION CLASSIFICATION
# ------------------------------------------------------------------

# ─────────────────────────────────────────────────────────────
# LOCATION CLASSIFICATION
#
# Single source of truth for all anatomical grouping logic.
# Used by selectors, retriever, engine_utils, and clinical_parser.
# Import from here — never redefine in another file.
# ─────────────────────────────────────────────────────────────

# Procedure location groups (excision, shave removal, destruction)
_FACE_TOKENS = {
    "face", "ear", "ears", "eyelid", "eyelids", "nose", "lip", "lips",
    "mucous", "cheek", "forehead", "temple", "chin", "jaw",
    "nasal", "perinasal", "perioral", "periorbital", "orbital",
    "auricular", "periauricular", "brow", "eyebrow", "malar",
    "mandibular", "labial", "buccal", "temporal", "zygomatic",
    "frontal", "glabella", "palpebral", "canthal",
}
_SPECIAL_TOKENS = {
    "scalp", "neck", "hand", "hands", "foot", "feet", "genitalia",
    "genital", "genitals", "axilla", "axillae",
    "palmar", "plantar", "digital", "finger", "toe", "inguinal",
    "cervical", "nuchal",
}

# Closure/repair location groups (different grouping system)
_CLOSURE_GROUPS = {
    "critical":    {"nose", "lip", "lips", "ear", "ears", "eyelid", "eyelids"},
    "high_risk":   {"face", "cheek", "forehead", "chin", "jaw", "temple",
                    "hand", "hands", "foot", "feet", "neck", "genitalia",
                    "axilla", "axillae", "mouth"},
    "extremities": {"scalp", "arm", "forearm", "leg", "foreleg"},
    # trunk is the default — any unmatched location
}

# Mohs risk classification (2-group)
_MOHS_HIGH_RISK_TOKENS = {
    "head", "neck", "temple", "face", "jaw", "scalp", "ear", "ears",
    "eyelid", "eyelids", "nose", "lip", "lips", "hand", "hands",
    "foot", "feet", "genitalia", "genital", "auricle",
}

# Keywords used to match CPT code DESCRIPTIONS for location filtering
LOCATION_DESC_KEYWORDS: dict[str, list[str]] = {
    "face":        ["face", "ear", "eyelid", "nose", "lip", "mucous membrane"],
    "special":     ["scalp", "neck", "hand", "foot", "feet", "genitalia"],
    "trunk":       ["trunk", "arm", "leg"],
    "critical":    ["nose", "lip", "ear", "eyelid"],
    "high_risk":   ["face", "axillae", "hand", "foot", "feet", "genitalia",
                    "neck", "chin", "cheek", "forehead", "mouth"],
    "extremities": ["scalp", "arm", "leg"],
}


def classify_location(location_text: str) -> str:
    """
    Map a free-text anatomical location to one of three procedure groups:
      'face'    — face / ears / eyelids / nose / lips / mucous membrane
      'special' — scalp / neck / hands / feet / genitalia
      'trunk'   — trunk / arms / legs (default)
    """
    if not location_text:
        return "trunk"
    tokens = set(location_text.lower().split())
    if tokens & _FACE_TOKENS:
        return "face"
    if tokens & _SPECIAL_TOKENS:
        return "special"
    return "trunk"


def classify_closure_location(location_text: str) -> str:
    """
    Map a free-text location to a closure/repair location group:
      'critical'    — nose / lips / ears / eyelids
      'high_risk'   — face / hands / feet / neck / genitalia
      'extremities' — scalp / arms / legs
      'trunk'       — trunk / back / chest / abdomen (default)
    """
    if not location_text:
        return "trunk"
    loc = location_text.lower()
    for group, keywords in _CLOSURE_GROUPS.items():
        if any(kw in loc for kw in keywords):
            return group
    return "trunk"


def classify_mohs_risk(location_text: str) -> str:
    """
    Map a location to Mohs risk tier:
      'high_risk'        — head / neck / face / ears / eyelids / nose / lips / hands / feet / genitalia
      'trunk_extremity'  — trunk / extremities (default)
    """
    if not location_text:
        return "trunk_extremity"
    tokens = set(location_text.lower().split())
    if tokens & _MOHS_HIGH_RISK_TOKENS:
        return "high_risk"
    return "trunk_extremity"


def match_desc_by_location(candidates: List[dict], location_group: str) -> List[dict]:
    """
    Filter candidates by matching location keywords against their CPT descriptions.
    Uses LOCATION_DESC_KEYWORDS — the same keyword sets used across all selectors.
    Falls back to all candidates if no match.
    """
    keywords = LOCATION_DESC_KEYWORDS.get(location_group, [])
    if not keywords:
        return candidates
    filtered = [
        r for r in candidates
        if any(k in (r.get("description") or "").lower() for k in keywords)
    ]
    return filtered


# ------------------------------------------------------------------
# SIZE-RANGE MATCHING
# ------------------------------------------------------------------

_EPSILON = 0.005  # half a millimetre tolerance for float rounding


def match_by_size(
    candidates: List[dict],
    size: float,
    location_group: Optional[str] = None,
) -> Optional[dict]:

    pool = _filter_by_location(candidates, location_group) if location_group else candidates

    for row in pool:
        min_s = float(row["minSize"])
        max_s = float(row["maxSize"])

        if min_s <= size <= max_s + _EPSILON:
            return row
    
    logger.warning(
        f"No size match: "
        f"size={size}, "
        f"group={location_group}, "
        f"candidates={[r['code'] for r in pool]}"
    )

    return None

def _filter_by_location(candidates: List[dict], location_group: str) -> List[dict]:
    return match_desc_by_location(candidates, location_group)


# ------------------------------------------------------------------
# QUANTITY-RANGE MATCHING
# ------------------------------------------------------------------

def match_by_qty(candidates: List[dict], quantity: int) -> List[dict]:
    """Return all codes whose [minQty, maxQty] range contains quantity."""
    return [r for r in candidates if r["minQty"] <= quantity <= r["maxQty"]]


# ------------------------------------------------------------------
# OUTPUT BUILDER
# ------------------------------------------------------------------

def make_code(
    row: dict,
    quantity: int = 1,
    source: str = "",
    confidence: str = "confirmed",
    selection_data: dict | None = None,
) -> dict:
    """
    Produce a standardised code dict for the pipeline.

    selection_data carries the exact inputs that drove this selection —
    size, quantity, location_group, method, etc. — so the reasoning
    engine can later explain every decision without guessing.
    """
    return {
        "code": row["code"],
        "description": row["description"],
        "proName": row["proName"],
        "type": "cpt",
        "quantity": str(quantity),
        "confidence": confidence,
        "source": source,
        "associatedWithProCode": row["associatedWithProCode"],
        "minSize": row["minSize"],
        "maxSize": row["maxSize"],
        "modifier": None,
        "linked_dx": [],
        "selection_data": selection_data or {},
    }
