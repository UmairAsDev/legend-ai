# services/code_selectors/base.py

import csv
import math
from pathlib import Path
from typing import Dict, List, Optional

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
                rows.append({
                    "code": str(row["proCode"]).strip(),
                    "description": str(row.get("codeDesc", "")).strip(),
                    "proName": str(row.get("proName", "")).strip(),
                    "type": "cpt",
                    "associatedWithProCode": _normalise_assoc(row.get("associatedWithProCode")),
                    "minSize": _f(row.get("minSize")),
                    "maxSize": _f(row.get("maxSize")),
                    "minQty": _i(row.get("minQty")),
                    "maxQty": _i(row.get("maxQty")),
                    "addOn": str(row.get("addOn", "0")).strip() == "1",
                    "billWithIntEM": str(row.get("billWithIntEM", "0")).strip() == "1",
                    "billWithFUEM": str(row.get("billWithFUEM", "0")).strip() == "1",
                    "billAlone": str(row.get("billAlone", "0")).strip() == "1",
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

_FACE_TOKENS = {
    # Nouns
    "face", "ear", "ears", "eyelid", "eyelids", "nose", "lip", "lips",
    "mucous", "cheek", "forehead", "temple", "chin", "jaw",
    # Anatomical adjectives (common in clinical notes)
    "nasal", "perinasal", "perioral", "periorbital", "orbital",
    "auricular", "periauricular", "brow", "eyebrow", "malar",
    "mandibular", "labial", "buccal", "temporal", "zygomatic",
    "frontal", "glabella", "palpebral", "canthal",
}
_SPECIAL_TOKENS = {
    "scalp", "neck", "hand", "hands", "foot", "feet", "genitalia",
    "genital", "genitals", "axilla", "axillae",
    # Anatomical adjectives
    "palmar", "plantar", "digital", "finger", "toe", "inguinal",
    "cervical", "nuchal",
}


def classify_location(location_text: str) -> str:
    """
    Map a free-text anatomical location to one of three groups:
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


# ------------------------------------------------------------------
# SIZE-RANGE MATCHING
# ------------------------------------------------------------------

_EPSILON = 0.005  # half a millimetre tolerance for float rounding


def match_by_size(
    candidates: List[dict],
    size: float,
    location_group: Optional[str] = None,
) -> Optional[dict]:
    """
    Return the single candidate whose [minSize, maxSize] range contains size.

    A small epsilon (0.005 cm) is applied to maxSize comparisons to absorb
    floating-point rounding from the parser (e.g. 2.0000000001 should match
    the 1.1-2.0 bracket, not overflow to the next one).
    """
    pool = _filter_by_location(candidates, location_group) if location_group else candidates

    for row in pool:
        min_s = float(row["minSize"])
        max_s = float(row["maxSize"])
        if min_s <= size <= max_s + _EPSILON:
            return row

    if pool:
        return max(pool, key=lambda r: float(r["maxSize"]))
    return None


def _filter_by_location(candidates: List[dict], location_group: str) -> List[dict]:
    _DESC_KEYWORDS = {
        "face": {"face", "ear", "eyelid", "nose", "lip", "mucous"},
        "special": {"scalp", "neck", "hand", "foot", "feet", "genitalia"},
        "trunk": {"trunk", "arm", "leg"},
    }
    keywords = _DESC_KEYWORDS.get(location_group, set())
    if not keywords:
        return candidates
    filtered = [
        r for r in candidates
        if any(k in r["description"].lower() for k in keywords)
    ]
    return filtered if filtered else candidates


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
