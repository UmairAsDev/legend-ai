# services/code_selectors/destruction_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import (
    classify_location, load_codes_by_name, make_code,
    match_by_qty, match_by_size, match_desc_by_location,
)

_DPM_NAME = "Destruction Premalignant Lesion"
_DBM_NAME = "Destruction Benign"
_DM_NAME  = "Destruction Malignant Lesion"
_DVP_NAME = "Destruction Vascular Proliferative Lesion"


class DestructionSelector:
    """
    Deterministic CPT selection for all destruction subtypes.

    All quantity boundaries and size ranges come from proCodeList.csv
    (minQty / maxQty / minSize / maxSize) — nothing is hardcoded here.
    Add-on codes are identified by associatedWithProCode != null.
    """

    # ── DPM (Premalignant / Actinic Keratosis) ─────────────────────

    @classmethod
    def select_dpm(cls, quantity: int) -> List[dict]:
        if quantity <= 0:
            return []

        codes    = load_codes_by_name(_DPM_NAME)
        primaries = sorted(
            [c for c in codes if not c["associatedWithProCode"]],
            key=lambda c: c["minQty"] or 0,
        )
        addons   = [c for c in codes if c["associatedWithProCode"]]

        sd = {"quantity": quantity, "subtype": "dpm"}

        # Try a direct qty-range match on primary codes first.
        # This handles standalone codes (e.g., the 15+ lesion code) whose range
        # starts high enough to exclude the primary+add-on codes.
        direct = match_by_qty(primaries, quantity)
        if direct:
            row = direct[0]
            logger.info(f"DestructionSelector DPM: {row['code']} qty={quantity}")
            return [make_code(row, quantity=1, source="destruction_dpm", selection_data=sd)]

        # No standalone match — use lowest-range primary + add-on for extras
        base = primaries[0] if primaries else None
        if not base:
            return []

        result = [make_code(base, quantity=1, source="destruction_dpm", selection_data=sd)]

        if quantity > 1:
            addon_pool = [a for a in addons if a["associatedWithProCode"] == base["code"]]
            if addon_pool:
                addon_qty = quantity - 1
                result.append(make_code(
                    addon_pool[0], quantity=addon_qty, source="destruction_dpm",
                    selection_data={**sd, "addon_quantity": addon_qty},
                ))

        logger.info(f"DestructionSelector DPM: {[r['code'] for r in result]} qty={quantity}")
        return result

    # ── DBM (Destruction Benign) ────────────────────────────────────

    @classmethod
    def select_dbm(cls, quantity: int) -> List[dict]:
        if quantity <= 0:
            return []

        codes   = load_codes_by_name(_DBM_NAME)
        matched = match_by_qty(codes, quantity)
        row     = matched[0] if matched else (codes[0] if codes else None)
        if not row:
            return []

        logger.info(f"DestructionSelector DBM: {row['code']} qty={quantity}")
        return [make_code(row, quantity=quantity, source="destruction_db",
                          selection_data={"quantity": quantity, "subtype": "dbm"})]

    # ── DM (Destruction Malignant) ──────────────────────────────────

    @classmethod
    def select_dm(
        cls,
        size: Optional[float],
        location: Optional[str],
        quantity: int = 1,
    ) -> List[dict]:
        candidates    = load_codes_by_name(_DM_NAME)
        location_group = classify_location(location or "")
        sd = {"size_cm": size, "location": location, "location_group": location_group,
              "quantity": quantity, "subtype": "dm"}

        # Filter by location group via CPT description keywords
        pool = match_desc_by_location(candidates, location_group)

        if size is None:
            # No size — pick the smallest code for this location group
            row = min(pool, key=lambda c: float(c["minSize"]), default=None)
            if row:
                logger.info(f"DestructionSelector DM: {row['code']} (no size, {location_group})")
                return [make_code(row, quantity=quantity, source="destruction_dm",
                                  confidence="inferred", selection_data=sd)]
            return []

        match = match_by_size(pool, float(size), location_group)
        if not match:
            return []

        logger.info(f"DestructionSelector DM: {match['code']} size={size} loc={location_group}")
        return [make_code(match, quantity=quantity, source="destruction_dm",
                          selection_data={**sd, "bracket_min": match["minSize"],
                                          "bracket_max": match["maxSize"]})]

    # ── DVP (Destruction Vascular Proliferative) ────────────────────

    @classmethod
    def select_dvp(cls, area: Optional[float]) -> List[dict]:
        if area is None:
            return []
        candidates = load_codes_by_name(_DVP_NAME)
        match = match_by_size(candidates, float(area))
        if not match:
            return []
        logger.info(f"DestructionSelector DVP: {match['code']} area={area}")
        return [make_code(match, quantity=1, source="destruction_dvp")]

    # ── DISPATCH ────────────────────────────────────────────────────

    @classmethod
    def select(
        cls,
        destruction_type: str,
        quantity: int = 1,
        size: Optional[float] = None,
        location: Optional[str] = None,
    ) -> List[dict]:
        dtype = (destruction_type or "").lower()
        if dtype == "dpm":
            return cls.select_dpm(quantity)
        if dtype in ("db", "dbm"):
            return cls.select_dbm(quantity)
        if dtype == "dm":
            return cls.select_dm(size, location, quantity)
        if dtype in ("dvp", "vascular"):
            return cls.select_dvp(size)
        logger.warning(f"DestructionSelector: unknown type '{destruction_type}'")
        return []
