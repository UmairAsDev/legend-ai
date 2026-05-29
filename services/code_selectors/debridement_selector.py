# services/code_selectors/debridement_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code

_PRO_NAME = "Debridement"

_NAIL_CODES = {"1-5": "11720", "6+": "11721"}
_DERM_CODE = "11000"
_WOUND_DEPTH_CODES = {
    "partial": "11040",
    "superficial": "11040",
    "shave": "11040",
    "full": "11041",
    "subcutaneous": "11042",
}
_DEFAULT_WOUND_CODE = "11040"


class DebridementSelector:
    """
    Deterministic CPT selection for debridement.

    Nail debridement:
      11720 — 1–5 nails
      11721 — 6 or more nails

    Dermatologic (eczematous/infected/crusted skin, not a wound):
      11000

    Wound debridement (by depth):
      11040 — partial thickness / superficial
      11041 — full thickness
      11042 — subcutaneous tissue
      Default: 11040 when depth is unknown
    """

    @classmethod
    def select(
        cls,
        nail: bool = False,
        dermatologic: bool = False,
        is_wound: bool = False,
        depth: Optional[str] = None,
        quantity: int = 1,
    ) -> List[dict]:
        all_codes = load_codes_by_name(_PRO_NAME)
        code_map = {r["code"]: r for r in all_codes}

        sd = {"nail": nail, "dermatologic": dermatologic, "is_wound": is_wound,
              "depth": depth, "quantity": quantity}

        if nail:
            target = _NAIL_CODES["6+"] if quantity >= 6 else _NAIL_CODES["1-5"]
            row = code_map.get(target)
            if row:
                logger.info(f"DebridementSelector: {target}  nail  qty={quantity}")
                return [make_code(row, quantity=1, source="debridement", selection_data=sd)]
            return []

        if dermatologic and not is_wound:
            row = code_map.get(_DERM_CODE)
            if row:
                logger.info(f"DebridementSelector: {_DERM_CODE}  dermatologic")
                return [make_code(row, quantity=quantity, source="debridement", selection_data=sd)]
            return []

        depth_key = (depth or "").lower()
        target = _WOUND_DEPTH_CODES.get(depth_key, _DEFAULT_WOUND_CODE)
        row = code_map.get(target)
        if row:
            logger.info(f"DebridementSelector: {target}  depth={depth_key}  qty={quantity}")
            return [make_code(row, quantity=quantity, source="debridement", selection_data=sd)]

        return []
