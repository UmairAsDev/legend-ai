# services/code_selectors/debridement_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code, match_by_qty

_PRO_NAME = "Debridement"

# Depth keywords mapped to description fragments in the CPT code text.
# Used to identify the right wound depth code from the database row descriptions
# rather than hardcoding specific CPT code numbers.
_DEPTH_DESC_KEYWORDS = {
    "partial":       ["partial thickness", "epidermis", "superficial"],
    "superficial":   ["partial thickness", "epidermis", "superficial"],
    "shave":         ["partial thickness", "epidermis", "superficial"],
    "full":          ["full thickness", "dermis"],
    "subcutaneous":  ["subcutaneous"],
}
_DEFAULT_DEPTH_KEYWORD = "partial thickness"


class DebridementSelector:
    """
    Deterministic CPT selection for debridement.

    Nail, dermatologic, and wound debridement are identified by proName.
    Nail and wound quantity ranges come from minQty/maxQty in proCodeList.csv.
    Wound depth code is matched by description keyword — no CPT code hardcoded.
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
        sd = {"nail": nail, "dermatologic": dermatologic, "is_wound": is_wound,
              "depth": depth, "quantity": quantity}

        # ── Nail debridement ─────────────────────────────────────────
        if nail:
            nail_codes = [c for c in all_codes
                          if "nail" in (c.get("description") or "").lower()]
            matched = match_by_qty(nail_codes, quantity)
            row = matched[0] if matched else (nail_codes[0] if nail_codes else None)
            if row:
                logger.info(f"DebridementSelector: {row['code']} nail qty={quantity}")
                return [make_code(row, quantity=1, source="debridement", selection_data=sd)]
            return []

        # ── Dermatologic debridement (eczematous/infected/crusted, not a wound) ──
        if dermatologic and not is_wound:
            derm_codes = [c for c in all_codes
                          if "eczema" in (c.get("description") or "").lower()
                          or "infected" in (c.get("description") or "").lower()
                          or "crusted" in (c.get("description") or "").lower()
                          or "dermatolog" in (c.get("description") or "").lower()]
            row = derm_codes[0] if derm_codes else None
            if row:
                logger.info(f"DebridementSelector: {row['code']} dermatologic")
                return [make_code(row, quantity=quantity, source="debridement", selection_data=sd)]
            return []

        # ── Wound debridement — matched by depth description ─────────
        depth_key     = (depth or "").lower()
        desc_keywords = _DEPTH_DESC_KEYWORDS.get(depth_key, [_DEFAULT_DEPTH_KEYWORD])

        wound_codes = [c for c in all_codes
                       if not any(word in (c.get("description") or "").lower()
                                  for word in ("nail", "eczema", "infected", "crusted"))]

        matched_by_depth = [
            c for c in wound_codes
            if any(kw in (c.get("description") or "").lower() for kw in desc_keywords)
        ]

        row = matched_by_depth[0] if matched_by_depth else (wound_codes[0] if wound_codes else None)
        if row:
            logger.info(f"DebridementSelector: {row['code']} depth={depth_key or 'unknown'}")
            return [make_code(row, quantity=quantity, source="debridement", selection_data=sd)]

        return []
