# services/code_selectors/biopsy_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code

# 2019+ biopsy codes by method
_METHOD_CODES = {
    "tangential": {"primary": "11102", "addon": "11103"},
    "shave":      {"primary": "11102", "addon": "11103"},
    "punch":      {"primary": "11104", "addon": "11105"},
    "incisional": {"primary": "11106", "addon": "11107"},
    "incision":   {"primary": "11106", "addon": "11107"},
}

# Legacy fallback when method is unknown
_LEGACY_PRIMARY = "11102"
_LEGACY_ADDON = "11103"

_PRO_NAME = "Biopsy"


class BiopsySelector:
    """
    Deterministic CPT selection for skin biopsies.

    2019+ codes:
      Tangential (shave):  11102 (first) + 11103 (each additional)
      Punch:               11104 (first) + 11105 (each additional)
      Incisional:          11106 (first) + 11107 (each additional)

    When method is absent, defaults to tangential (most common in dermatology).
    """

    @classmethod
    def select(cls, method: Optional[str], count: int = 1) -> List[dict]:
        if count <= 0:
            return []

        method_key = (method or "").lower().strip()
        method_codes = _METHOD_CODES.get(method_key)

        if not method_codes:
            # Default to tangential
            primary_code = _LEGACY_PRIMARY
            addon_code = _LEGACY_ADDON
            confidence = "inferred"
        else:
            primary_code = method_codes["primary"]
            addon_code = method_codes["addon"]
            confidence = "confirmed"

        all_codes = load_codes_by_name(_PRO_NAME)
        code_map = {r["code"]: r for r in all_codes}

        primary_row = code_map.get(primary_code)
        if not primary_row:
            logger.debug(f"BiopsySelector: code {primary_code} not found in DB")
            return []

        sd = {"method": method_key or None, "count": count}
        result = [make_code(primary_row, quantity=1, source="biopsy", confidence=confidence,
                            selection_data=sd)]

        if count > 1:
            addon_row = code_map.get(addon_code)
            if addon_row:
                result.append(
                    make_code(addon_row, quantity=count - 1, source="biopsy", confidence=confidence,
                              selection_data={**sd, "addon_quantity": count - 1})
                )

        logger.info(
            f"BiopsySelector: {[r['code'] for r in result]}  "
            f"method={method_key or 'default'}  count={count}"
        )
        return result
