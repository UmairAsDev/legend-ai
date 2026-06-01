# services/code_selectors/att_selector.py
"""
Deterministic CPT selector for Adjacent Tissue Transfer / Rearrangement (ATT).

ATT codes (14000-14350) are selected by:
  1. Defect size (sq cm) — matches minSize / maxSize from proCodeList.csv
  2. Location group    — matches keywords in the CPT code description

proName in proCodeList.csv: "Adjacent Tissue Transfer"

All code selection is driven by proCodeList.csv data via the KnowledgeBase.
No CPT code numbers are hardcoded here.
"""

from typing import List, Optional
from loguru import logger

from services.code_selectors.base import (
    classify_closure_location,
    load_codes_by_name,
    make_code,
    match_by_size,
    match_desc_by_location,
)

_PRO_NAME = "Adjacent Tissue Transfer"


class AttSelector:
    """
    Deterministic CPT selection for adjacent tissue transfer / rearrangement.

    Inputs:
      defect_size_cm2  — post-operative defect area in square centimetres
      location         — free-text anatomical location from the note

    The selector maps location → closure location group ("critical", "high_risk",
    "extremities", "trunk") using the same classification used by ClosureSelector,
    then filters ATT candidates by description keyword and size range.
    """

    @classmethod
    def select(
        cls,
        defect_size_cm2: Optional[float],
        location: Optional[str] = None,
        location_group: Optional[str] = None,
    ) -> List[dict]:
        """
        Select an ATT code.

        Args:
            defect_size_cm2: post-operative defect area in square centimetres.
            location:        raw free-text location from the note (used to derive
                             location_group when location_group is not provided).
            location_group:  pre-classified group name ("critical", "high_risk",
                             "extremities", "trunk") — use this when the caller
                             has already classified the location to avoid double
                             classification.  Takes precedence over location.
        """
        if not defect_size_cm2 or defect_size_cm2 <= 0:
            logger.debug("AttSelector: no defect size — skipping")
            return []

        all_codes = load_codes_by_name(_PRO_NAME)
        if not all_codes:
            logger.warning("AttSelector: no codes found for 'Adjacent Tissue Transfer' in proCodeList.csv")
            return []

        if not location_group:
            location_group = classify_closure_location(location or "")

        # Filter by location keywords in description (face/neck/hands vs trunk/arms, etc.)
        pool = match_desc_by_location(all_codes, location_group)

        primary = match_by_size(pool, defect_size_cm2, location_group)
        if not primary:
            # Fallback: ignore location filter, pick by size only
            primary = match_by_size(all_codes, defect_size_cm2)

        if not primary:
            logger.warning(
                f"AttSelector: no match  size={defect_size_cm2}cm²  "
                f"loc={location!r}  group={location_group}"
            )
            return []

        sd = {
            "defect_size_cm2": defect_size_cm2,
            "location":        location,
            "location_group":  location_group,
            "bracket_min":     primary["minSize"],
            "bracket_max":     primary["maxSize"],
        }
        result = [make_code(primary, quantity=1, source="att", selection_data=sd)]

        logger.info(
            f"AttSelector: {result[0]['code']}  "
            f"size={defect_size_cm2}cm²  loc={location_group}"
        )
        return result
