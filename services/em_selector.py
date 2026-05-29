# services/em_selector.py

import csv
from datetime import date
from pathlib import Path
from typing import Optional, Dict
from loguru import logger

_EM_CODES: list[dict] = []
_DATA_PATH = Path(__file__).parent.parent / "data" / "enmCodeList.csv"


def _load_em_codes():
    global _EM_CODES
    if _EM_CODES:
        return
    with open(_DATA_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            _EM_CODES.append(dict(row))
    logger.info(f"✅ E/M codes loaded: {len(_EM_CODES)} records")


def _is_active(row: dict) -> bool:
    if str(row.get("deleted", "0")).strip() == "1":
        return False
    expire_raw = str(row.get("expireDate", "12/31/2050")).strip()
    try:
        parts = expire_raw.split("/")
        expire_iso = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
        return expire_iso >= date.today().isoformat()
    except Exception:
        return True


def select_em_code(
    patient_type: str,
    encounter_time: Optional[int] = None,
    em_level: Optional[int] = None,
) -> Optional[Dict]:
    """
    Deterministically select an E/M code from enmCodeList.csv.

    patient_type : 'newPat' | 'estPat' | 'consult' | 'other'
    encounter_time : documented visit time in minutes (takes priority)
    em_level : explicit level 1-5 (used when time is absent)

    Returns the matching row dict or None if no match is possible.
    """
    _load_em_codes()

    candidates = [
        r for r in _EM_CODES
        if r.get("enmType") == patient_type and _is_active(r)
    ]

    if not candidates:
        logger.warning(f"⚠️ No active E/M codes found for type={patient_type}")
        return None

    # Sort by level ascending so we can walk through thresholds
    candidates.sort(key=lambda r: int(r.get("enmLevel") or 0))

    # Time-based: select highest level whose encounterTime ≤ documented time
    if encounter_time is not None:
        selected = None
        for r in candidates:
            threshold = int(r.get("encounterTime") or 0)  # already int from csv_handler
            if threshold <= encounter_time:
                selected = r
        if selected:
            logger.info(
                f"✅ E/M selected by time: {selected['enmCode']} "
                f"(type={patient_type}, time={encounter_time}min)"
            )
            return selected

    # Level-based fallback
    if em_level is not None:
        for r in candidates:
            if int(r.get("enmLevel") or 0) == em_level:
                logger.info(
                    f"✅ E/M selected by level: {r['enmCode']} "
                    f"(type={patient_type}, level={em_level})"
                )
                return r

    logger.info(f"ℹ️ No E/M match for type={patient_type} time={encounter_time} level={em_level}")
    return None
