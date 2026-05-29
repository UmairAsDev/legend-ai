# services/code_selectors/shave_removal_selector.py

from typing import List, Optional
from loguru import logger
from services.code_selectors.base import (
    load_codes_by_name,
    make_code,
    match_by_size,
)

_PRO_NAME = "Shave Removal"


class ShaveRemovalSelector:
    """
    Deterministic CPT selection for shave removal procedures.

    IMPORTANT:
    This selector should only be called when the procedure has already
    been classified as a true shave removal (CPT 11300-11313).

    Do NOT use for shave biopsies (11102-11107).
    """

    @classmethod
    def select(
        cls,
        size: Optional[float],
        location_group: Optional[str],
        procedure_type: Optional[str] = None,
    ) -> List[dict]:

        # Prevent biopsy notes from generating shave-removal codes
        if procedure_type not in {
            "shave_removal",
            "shave_excision",
        }:
            logger.debug(
                f"ShaveRemovalSelector skipped: procedure_type={procedure_type}"
            )
            return []

        candidates = load_codes_by_name(_PRO_NAME)
        group = location_group or "trunk"

        match = None

        # Require lesion size for shave-removal coding
        if size is None:
            logger.warning(
                "ShaveRemovalSelector: missing lesion size; unable to determine CPT"
            )
            return []

        match = match_by_size(
            candidates,
            float(size),
            group,
        )

        if not match:
            logger.debug(
                f"ShaveRemovalSelector: no match size={size} group={group}"
            )
            return []

        logger.info(
            f"ShaveRemovalSelector: {match['code']} size={size} group={group}"
        )

        return [
            make_code(
                match,
                quantity=1,
                source="shave_removal",
                selection_data={
                    "size_cm": size,
                    "location_group": group,
                    "bracket_min": match["minSize"],
                    "bracket_max": match["maxSize"],
                },
            )
        ]


def _group_matches(description: str, group: str) -> bool:
    desc = description.lower()

    if group == "face":
        return any(
            k in desc
            for k in (
                "face",
                "ear",
                "eyelid",
                "nose",
                "lip",
                "mucous",
            )
        )

    if group == "special":
        return any(
            k in desc
            for k in (
                "scalp",
                "neck",
                "hand",
                "foot",
                "genitalia",
            )
        )

    return any(
        k in desc
        for k in (
            "trunk",
            "arm",
            "leg",
        )
    )