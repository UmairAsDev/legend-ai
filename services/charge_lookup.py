# services/charge_lookup.py
"""
Charge-per-unit flag lookup loaded once from proCodeList.csv.

chargePerUnit is a boolean flag per CPT code:
  1  → Yes, this procedure is charged per unit billed
  0  → No charge-per-unit billing for this code

Loaded once at startup and cached for the lifetime of the process.
"""

import csv
from pathlib import Path
from typing import Dict

_DATA_PATH = Path(__file__).parent.parent / "data" / "proCodeList.csv"
_CHARGE_MAP: Dict[str, bool] = {}
_LOADED = False


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    with open(_DATA_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = str(row.get("proCode", "")).strip()
            raw = str(row.get("chargePerUnit", "0")).strip()
            try:
                _CHARGE_MAP[code] = bool(int(float(raw)))
            except (ValueError, TypeError):
                _CHARGE_MAP[code] = False
    _LOADED = True


def is_charge_per_unit(cpt_code: str) -> bool:
    """Return True if this CPT code is flagged as charge-per-unit in proCodeList."""
    _load()
    return _CHARGE_MAP.get(str(cpt_code).strip(), False)
