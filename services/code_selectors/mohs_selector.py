# services/code_selectors/mohs_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code

_PRO_NAME = "MOHS Micrographic Surgery"

_HIGH_RISK_TOKENS = {
    "head", "neck", "temple", "face", "jaw", "scalp", "ear", "ears",
    "eyelid", "eyelids", "nose", "lip", "lips", "hand", "hands",
    "foot", "feet", "genitalia", "genital", "auricle",
}

# High-risk codes
_HR_FIRST = "17311"
_HR_ADDON = "17312"

# Trunk / extremities codes
_TR_FIRST = "17313"
_TR_ADDON = "17314"


class MohsSelector:
    """
    Deterministic CPT selection for Mohs micrographic surgery.

    High-risk locations (head, neck, face, ears, eyelids, nose, lips,
    hands, feet, genitalia):
      17311 — first stage (qty=1)
      17312 — each additional stage (qty = stages - 1)

    Trunk / extremities:
      17313 — first stage (qty=1)
      17314 — each additional stage (qty = stages - 1)
    """

    @classmethod
    def select(cls, location: Optional[str], stages: int = 1) -> List[dict]:
        if stages <= 0:
            return []

        location_tokens = set((location or "").lower().split())
        is_high_risk = bool(location_tokens & _HIGH_RISK_TOKENS)

        first_code = _HR_FIRST if is_high_risk else _TR_FIRST
        addon_code = _HR_ADDON if is_high_risk else _TR_ADDON

        all_codes = load_codes_by_name(_PRO_NAME)
        code_map = {r["code"]: r for r in all_codes}

        first_row = code_map.get(first_code)
        if not first_row:
            logger.debug(f"MohsSelector: code {first_code} not found in DB")
            return []

        sd = {"location": location, "stages": stages, "is_high_risk": is_high_risk}
        result = [make_code(first_row, quantity=1, source="mohs", selection_data=sd)]

        if stages > 1:
            addon_row = code_map.get(addon_code)
            if addon_row:
                result.append(make_code(addon_row, quantity=stages - 1, source="mohs",
                                        selection_data={**sd, "additional_stages": stages - 1}))

        logger.info(
            f"MohsSelector: {[r['code'] for r in result]}  "
            f"location={location}  stages={stages}  high_risk={is_high_risk}"
        )
        return result
