# services/code_selectors/xtrac_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code, match_by_size

_PRO_NAME = "Xtrac Laser Treatment"
_DEFAULT_CODE = "96920"


class XtracSelector:
    """
    Deterministic CPT selection for Xtrac laser treatment.

    Area ranges (sq cm):
      96920 — < 250
      96921 — 250–500
      96922 — > 500

    Defaults to 96920 when area is absent or unknown.
    """

    @classmethod
    def select(cls, total_area: Optional[float]) -> List[dict]:
        candidates = load_codes_by_name(_PRO_NAME)

        if total_area is None:
            default = next((c for c in candidates if c["code"] == _DEFAULT_CODE), None)
            if default:
                logger.info(f"XtracSelector: {_DEFAULT_CODE} (default, no area)")
                return [make_code(default, quantity=1, source="xtrac", confidence="inferred",
                                  selection_data={"area_sqcm": None})]
            return []

        match = match_by_size(candidates, float(total_area))
        if not match:
            logger.debug(f"XtracSelector: no match for area={total_area}")
            return []

        logger.info(f"XtracSelector: {match['code']}  area={total_area}")
        return [make_code(match, quantity=1, source="xtrac",
                          selection_data={"area_sqcm": total_area,
                                          "bracket_min": match["minSize"],
                                          "bracket_max": match["maxSize"]})]
