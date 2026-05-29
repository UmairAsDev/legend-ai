# services/code_selectors/destruction_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import (
    classify_location, load_codes_by_name, make_code, match_by_size,
)

_DPM_NAME = "Destruction Premalignant Lesion"
_DBM_NAME = "Destruction Benign"
_DM_NAME = "Destruction Malignant Lesion"
_DVP_NAME = "Destruction Vascular Proliferative Lesion"


def _dm_location_keywords(location_group: str) -> list[str]:
    return {
        "face": ["face", "ear", "eyelid", "nose", "lip", "mucous"],
        "special": ["scalp", "neck", "hand", "foot", "genitalia"],
        "trunk": ["trunk", "arm", "leg"],
    }.get(location_group, ["trunk", "arm", "leg"])


class DestructionSelector:
    """
    Deterministic CPT selection for all destruction subtypes.

    DPM: 17000 + 17003 (add-on) or 17004 (15+ lesions)
    DBM: 17110 (up to 14) or 17111 (15+)
    DM:  17260-17286 — size + location
    DVP: 17106-17108 — area (sq cm)
    """

    @classmethod
    def select_dpm(cls, quantity: int) -> List[dict]:
        if quantity <= 0:
            return []

        codes = load_codes_by_name(_DPM_NAME)

        sd_dpm = {"quantity": quantity, "subtype": "dpm"}

        if quantity >= 15:
            row = next((c for c in codes if c["code"] == "17004"), None)
            if row:
                logger.info(f"DestructionSelector DPM: 17004  qty={quantity}")
                return [make_code(row, quantity=1, source="destruction_dpm", selection_data=sd_dpm)]
            return []

        base = next((c for c in codes if c["code"] == "17000"), None)
        if not base:
            return []

        result = [make_code(base, quantity=1, source="destruction_dpm", selection_data=sd_dpm)]
        if quantity > 1:
            addon = next((c for c in codes if c["code"] == "17003"), None)
            if addon:
                result.append(make_code(addon, quantity=quantity - 1, source="destruction_dpm",
                                        selection_data={**sd_dpm, "addon_quantity": quantity - 1}))

        logger.info(f"DestructionSelector DPM: {[r['code'] for r in result]}  qty={quantity}")
        return result

    @classmethod
    def select_dbm(cls, quantity: int) -> List[dict]:
        if quantity <= 0:
            return []

        codes = load_codes_by_name(_DBM_NAME)
        target = "17111" if quantity >= 15 else "17110"
        row = next((c for c in codes if c["code"] == target), None)
        if not row:
            return []

        logger.info(f"DestructionSelector DBM: {target}  qty={quantity}")
        return [make_code(row, quantity=quantity, source="destruction_db",
                          selection_data={"quantity": quantity, "subtype": "dbm"})]

    @classmethod
    def select_dm(
        cls,
        size: Optional[float],
        location: Optional[str],
        quantity: int = 1,
    ) -> List[dict]:
        candidates = load_codes_by_name(_DM_NAME)
        location_group = classify_location(location or "")
        sd_dm = {"size_cm": size, "location": location, "location_group": location_group,
                 "quantity": quantity, "subtype": "dm"}

        if size is None:
            kws = _dm_location_keywords(location_group)
            pool = [c for c in candidates if any(kw in c["description"].lower() for kw in kws)]
            pool = pool or candidates
            match = min(pool, key=lambda c: float(c["minSize"]))
            if match:
                logger.info(f"DestructionSelector DM: {match['code']} (no size, smallest for {location_group})")
                return [make_code(match, quantity=quantity, source="destruction_dm", confidence="inferred",
                                  selection_data={**sd_dm, "bracket_min": match["minSize"],
                                                  "bracket_max": match["maxSize"]})]
            return []

        match = match_by_size(candidates, float(size), location_group)
        if not match:
            logger.debug(f"DestructionSelector DM: no match  size={size}  loc={location_group}")
            return []

        logger.info(f"DestructionSelector DM: {match['code']}  size={size}  loc={location_group}  qty={quantity}")
        return [make_code(match, quantity=quantity, source="destruction_dm",
                          selection_data={**sd_dm, "bracket_min": match["minSize"],
                                          "bracket_max": match["maxSize"]})]

    @classmethod
    def select_dvp(cls, area: Optional[float]) -> List[dict]:
        if area is None:
            return []

        candidates = load_codes_by_name(_DVP_NAME)
        match = match_by_size(candidates, float(area))
        if not match:
            return []

        logger.info(f"DestructionSelector DVP: {match['code']}  area={area}")
        return [make_code(match, quantity=1, source="destruction_dvp")]

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
