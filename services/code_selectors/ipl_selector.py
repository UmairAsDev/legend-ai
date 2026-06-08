# services/code_selectors/ipl_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code, match_by_size

_PRO_NAME = "Intense Pulsed Light"

# Canonical method name → fragment to match in CPT description.
# Keys match the normalized_method strings produced by extract_ipl_sections.
_METHOD_DESC_KEYWORDS: dict[str, list[str]] = {
    "hair reduction":              ["hair reduction", "hair removal"],
    "tattoo removal":              ["tattoo"],
    "vein treatment":              ["vein treatment"],
    "spider veins treatment":      ["spider vein"],
    "skin rejuvenation":           ["skin rejuvenation", "rejuvenation"],
    "photorejuvenation treatment": ["photorejuvenation", "pigmented spots"],
    "rosacea treatment":           ["rosacea"],
    "melasma treatment":           ["melasma"],
    "acne treatment":              ["acne"],
    "birthmark treatment":         ["birthmark"],
    "photofacial":                 ["photofacial"],
}


class IplSelector:
    """
    Deterministic CPT/custom-code selection for Intense Pulsed Light.

    Two selection paths (in priority order):
      1. Area-based → standard CPT 96920/96921/96922 when total_area is known.
      2. Method-based → CI00x practice codes matched by description keyword.
      3. Fallback → first available code, confidence='fallback'.
    """

    @classmethod
    def select(
        cls,
        method: Optional[str],
        total_area: Optional[float] = None,
    ) -> List[dict]:
        all_codes = load_codes_by_name(_PRO_NAME)
        if not all_codes:
            logger.warning("IplSelector: no codes found for proName 'Intense Pulsed Light'")
            return []

        # PATH 1 — area-based (standard CPT codes have numeric minSize/maxSize)
        if total_area is not None:
            area_codes = [c for c in all_codes if c.get("maxSize") and float(c["maxSize"]) > 0]
            match = match_by_size(area_codes, float(total_area))
            if match:
                logger.info(f"IplSelector: area match {match['code']} area={total_area}")
                return [make_code(match, quantity=1, source="ipl",
                                  confidence="confirmed",
                                  selection_data={"total_area": total_area, "method": method})]

        # PATH 2 — method-based (CI00x custom codes)
        if method:
            method_lower = method.lower()
            keywords = _METHOD_DESC_KEYWORDS.get(method_lower)
            if not keywords:
                # Try partial match across all keys
                for key, kws in _METHOD_DESC_KEYWORDS.items():
                    if key in method_lower or any(k in method_lower for k in kws):
                        keywords = kws
                        break

            if keywords:
                matched = [
                    c for c in all_codes
                    if any(k in (c.get("description") or "").lower() for k in keywords)
                ]
                if matched:
                    logger.info(f"IplSelector: method match {matched[0]['code']} method={method!r}")
                    return [make_code(matched[0], quantity=1, source="ipl",
                                      confidence="confirmed",
                                      selection_data={"method": method, "total_area": total_area})]

        # PATH 3 — fallback: first code (smallest maxSize, or first in CSV order)
        fallback = sorted(
            [c for c in all_codes if not c.get("maxSize") or float(c.get("maxSize") or 0) == 0],
            key=lambda c: c.get("code") or "",
        ) or all_codes
        row = fallback[0]
        logger.info(f"IplSelector: fallback {row['code']} method={method!r} area={total_area}")
        return [make_code(row, quantity=1, source="ipl",
                          confidence="fallback",
                          selection_data={"method": method, "total_area": total_area})]
