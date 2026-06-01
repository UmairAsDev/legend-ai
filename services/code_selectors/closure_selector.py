# services/code_selectors/closure_selector.py

import math
from typing import List, Optional
from loguru import logger
from services.code_selectors.base import load_codes_by_name, make_code, match_by_size

_SIMPLE_NAME = "Simple Closure"
_INTERMEDIATE_NAME = "Layered Closure"
_COMPLEX_NAME = "Complex Closure"

_LOCATION_DESC_MAP = {
    "face":        {"face", "ear", "eyelid", "nose", "lip", "mucous"},
    "critical":    {"eyelid", "nose", "ear", "lip"},
    "high_risk":   {"face", "hand", "foot", "feet", "neck", "chin", "cheek", "genitalia", "axilla"},
    "special":     {"scalp", "neck", "hand", "foot", "feet", "genitalia", "axilla"},
    "extremities": {"scalp", "arm", "leg"},
    "trunk":       {"trunk", "back", "chest", "abdomen"},
}


class ClosureSelector:
    """
    Deterministic CPT selection for wound closures.

    Simple (12001-12018):     size + location (trunk vs face)
    Intermediate (12031-12055): size + location (trunk/scalp vs neck/hands/feet vs face)
    Complex (13100-13153):    size + location + optional add-on

    Returns primary code + add-on code when total_size exceeds primary.maxSize.
    """

    @classmethod
    def select(
        cls,
        total_size: float,
        closure_type: str,
        location_group: Optional[str],
    ) -> List[dict]:
        if total_size <= 0:
            return []

        ctype = (closure_type or "").lower()

        if ctype == "simple":
            return cls._select_from(
                _SIMPLE_NAME, total_size, location_group, add_on_enabled=False
            )
        if ctype in ("intermediate", "layered"):
            return cls._select_from(
                _INTERMEDIATE_NAME, total_size, location_group, add_on_enabled=False
            )
        if ctype == "complex":
            return cls._select_from(
                _COMPLEX_NAME, total_size, location_group, add_on_enabled=True
            )

        if ctype == "adjacent":
            # Adjacent tissue transfer is a distinct procedure family, not a closure.
            # Route to the ATT selector.  Pass location_group directly to avoid
            # re-classifying an already-classified group name.
            from services.code_selectors.att_selector import AttSelector
            return AttSelector.select(
                defect_size_cm2=total_size,
                location_group=location_group,
            )

        logger.warning(f"ClosureSelector: unknown closure type '{closure_type}'")
        return []

    @classmethod
    def _select_from(
        cls,
        pro_name: str,
        total_size: float,
        location_group: Optional[str],
        add_on_enabled: bool,
    ) -> List[dict]:
        candidates = load_codes_by_name(pro_name)

        # Separate base codes from add-on codes
        base_candidates = [c for c in candidates if not c["associatedWithProCode"]]
        addon_candidates = {c["associatedWithProCode"]: c for c in candidates if c["associatedWithProCode"]}

        primary = match_by_size(base_candidates, total_size, location_group)
        if not primary:
            logger.warning(
                f"Closure fallback triggered: "
                f"size={total_size}, "
                f"location_group={location_group}"
            )
            primary = match_by_size(base_candidates, total_size, None)
        if not primary:
            logger.debug(f"ClosureSelector: no primary match  size={total_size}  loc={location_group}")
            return []

        sd = {"total_size_cm": total_size, "closure_type": pro_name,
              "location_group": location_group, "bracket_min": primary["minSize"],
              "bracket_max": primary["maxSize"]}
        result = [make_code(primary, quantity=1, source="closure", selection_data=sd)]

        # Add-on: if total_size exceeds primary.maxSize, calculate add-on units
        if add_on_enabled:
            addon = addon_candidates.get(primary["code"])
            if addon and total_size > primary["maxSize"]:
                extra = total_size - primary["maxSize"]
                step = cls._addon_step(addon["description"])
                units = math.ceil(extra / step)
                if units > 0:
                    result.append(make_code(addon, quantity=units, source="closure",
                                            selection_data={**sd, "extra_cm": round(extra, 2),
                                                            "addon_step_cm": step}))

        logger.info(
            f"ClosureSelector: {[r['code'] for r in result]}  "
            f"type={pro_name}  size={total_size}  loc={location_group}"
        )
        return result

    @staticmethod
    def _addon_step(description: str) -> float:
        import re
        # Matches "each additional 5" or "each additional 5 cm" or "each additional 5.0"
        m = re.search(r"each additional\s+([\d]+\.?[\d]*)", description.lower())
        return float(m.group(1)) if m else 5.0
