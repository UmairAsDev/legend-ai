# services/code_selectors/excision_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import (
    classify_location, load_codes_by_name, make_code, match_by_size,
)

_BENIGN_NAMES = [
    "Excision Benign Lesion & Margins",
    "Excision Non Skin",
    "Soft Tissue Excision",
]
_MALIGNANT_NAME = "Excision Malignant Lesion & Margins"


class ExcisionSelector:
    """
    Deterministic CPT selection for excision procedures.

    Rules (from proCodeList.csv size ranges):
      Benign  — 11400-11446 (trunk / special / face)
      Malignant — 11600-11646 (trunk / special / face)

    Returns an empty list when required inputs are missing so the caller
    can fall back to the DB-based retriever.
    """

    @classmethod
    def select(
        cls,
        size: Optional[float],
        location: Optional[str],
        lesion_type: str = "benign",
    ) -> List[dict]:
        if size is None:
            logger.debug("ExcisionSelector: size missing — cannot select")
            return []

        pro_name = _MALIGNANT_NAME if lesion_type == "malignant" else _BENIGN_NAMES[0]
        candidates = load_codes_by_name(pro_name)

        location_group = classify_location(location or "")
        match = match_by_size(candidates, float(size), location_group)

        if not match:
            logger.debug(f"ExcisionSelector: no match size={size} loc={location_group}")
            return []

        logger.info(
            f"ExcisionSelector: {match['code']}  size={size}  "
            f"loc={location_group}  type={lesion_type}"
        )
        return [make_code(
            match,
            quantity=1,
            source="excision",
            selection_data={
                "size_cm": size,
                "location_group": location_group,
                "lesion_type": lesion_type,
                "bracket_min": match["minSize"],
                "bracket_max": match["maxSize"],
            },
        )]
