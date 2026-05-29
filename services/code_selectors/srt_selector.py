# services/code_selectors/srt_selector.py

import csv
from pathlib import Path
from typing import List, Optional
from loguru import logger
from services.code_selectors.base import _f, _normalise_assoc

_CSV_PATH = Path(__file__).parent.parent.parent / "data" / "proCodeList.csv"
_SRT_CODES = ("77436", "77437", "77438", "77439")
_ALWAYS_INCLUDE = "77436"
_LOW_KV = "77437"
_HIGH_KV = "77438"
_ULTRASOUND_ADDON = "77439"

_srt_cache: dict = {}


def _load_srt():
    if _srt_cache:
        return _srt_cache
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = str(row.get("proCode", "")).strip()
            if code in _SRT_CODES:
                _srt_cache[code] = {
                    "code": code,
                    "description": str(row.get("codeDesc", "")).strip(),
                    "proName": str(row.get("proName", "")).strip(),
                    "type": "cpt",
                    "associatedWithProCode": _normalise_assoc(row.get("associatedWithProCode")),
                    "minSize": _f(row.get("minSize")),
                    "maxSize": _f(row.get("maxSize")),
                }
    return _srt_cache


class SrtSelector:
    """
    Deterministic CPT selection for Surface Radiation Therapy (SRT / IGSRT).

      77436 — always (planning)
      77437 — delivery at kV <= 150 (or unknown)
      77438 — delivery at kV > 150
      77439 — add-on, only when ultrasound AND images documented
    """

    @classmethod
    def select(
        cls,
        kv: Optional[float],
        ultrasound: bool = False,
        images_present: bool = False,
    ) -> List[dict]:
        code_map = _load_srt()
        result: List[dict] = []

        def _add(code: str, confidence: str = "confirmed") -> None:
            row = code_map.get(code)
            if row:
                result.append({**row, "quantity": "1", "confidence": confidence,
                                "source": "srt", "modifier": None, "linked_dx": []})

        _add(_ALWAYS_INCLUDE)

        delivery = _HIGH_KV if (kv is not None and float(kv) > 150) else _LOW_KV
        _add(delivery)

        if ultrasound and images_present:
            _add(_ULTRASOUND_ADDON)

        logger.info(
            f"SrtSelector: {[r['code'] for r in result]}  "
            f"kv={kv}  us={ultrasound}  images={images_present}"
        )
        return result
