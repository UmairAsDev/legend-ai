# services/code_selectors/biopsy_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code

_PRO_NAME = "Biopsy"

# Method keywords mapped to description fragments in the CPT code text.
# The primary biopsy codes contain the method name in their descriptions
# (e.g., "tangential", "punch", "incisional") — no CPT numbers hardcoded.
_METHOD_DESC_KEYWORDS = {
    "tangential": "tangential",
    "shave":      "tangential",   # shave = tangential technique
    "punch":      "punch",
    "incisional": "incisional",
    "incision":   "incisional",
}
_DEFAULT_METHOD_KEYWORD = "tangential"   # most common in dermatology


class BiopsySelector:
    """
    Deterministic CPT selection for skin biopsies.

    Primary and add-on codes are identified by:
      - description keyword matching the biopsy method
      - associatedWithProCode == null  → primary
      - associatedWithProCode != null  → add-on

    No CPT code numbers are hardcoded — all codes come from proCodeList.csv.
    """

    @classmethod
    def select(cls, method: Optional[str], count: int = 1) -> List[dict]:
        if count <= 0:
            return []

        all_codes = load_codes_by_name(_PRO_NAME)
        primaries = [c for c in all_codes if not c["associatedWithProCode"]]
        addons    = [c for c in all_codes if c["associatedWithProCode"]]

        method_key   = (method or "").lower().strip()
        desc_keyword = _METHOD_DESC_KEYWORDS.get(method_key, _DEFAULT_METHOD_KEYWORD)
        confidence   = "confirmed" if method_key in _METHOD_DESC_KEYWORDS else "inferred"

        # Find the primary code matching this method's description keyword
        matched_primaries = [
            c for c in primaries
            if desc_keyword in (c.get("description") or "").lower()
        ]
        primary_row = matched_primaries[0] if matched_primaries else (primaries[0] if primaries else None)
        if not primary_row:
            logger.debug(f"BiopsySelector: no primary found for method={method_key!r}")
            return []

        sd = {"method": method_key or None, "count": count,
              "desc_keyword": desc_keyword}
        result = [make_code(primary_row, quantity=1, source="biopsy",
                            confidence=confidence, selection_data=sd)]

        if count > 1:
            # Find the add-on that pairs with this primary
            addon_pool = [
                a for a in addons
                if a["associatedWithProCode"] == primary_row["code"]
                or desc_keyword in (a.get("description") or "").lower()
            ]
            if addon_pool:
                addon_qty = count - 1
                result.append(make_code(
                    addon_pool[0], quantity=addon_qty, source="biopsy",
                    confidence=confidence,
                    selection_data={**sd, "addon_quantity": addon_qty},
                ))

        logger.info(
            f"BiopsySelector: {[r['code'] for r in result]} "
            f"method={method_key or 'default'} count={count}"
        )
        return result
