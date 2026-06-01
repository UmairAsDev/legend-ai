# services/boundary_checker.py
"""
Deterministic boundary case detection.

Replaces the LLM-based boundary detection that was in the billing_params prompt.
The LLM cannot reliably apply per-procedure boundaries and was cross-applying
excision boundaries to closure sizes (and vice versa), generating false flags.

This module uses minSize / maxSize from proCodeList.csv via the KnowledgeBase
to detect when a documented size falls near a code-range boundary.  Only sizes
genuinely close to a CPT boundary for THEIR OWN procedure family are flagged.

Produces entries in the same format as unresolved_procedures:
    {"description": "...", "reason": "boundary_case", "suggested_code": {...}}

The `suggested_code` field provides the best deterministic selection even in
the boundary zone, so the billing team can approve rather than recode from scratch.
"""

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from services.knowledge_base import kb


_EPSILON = 0.005     # float rounding tolerance
_DEFAULT_TOLERANCE = 0.3   # cm — flag if within this distance of a boundary


# ─────────────────────────────────────────────────────────────────────────────
# BOUNDARY DETECTION CORE
# ─────────────────────────────────────────────────────────────────────────────

def _find_boundary(
    pro_name: str,
    size: float,
    location_group: Optional[str],
    tolerance: float,
) -> Optional[Tuple[str, str, float]]:
    """
    Return (code_below, code_above, boundary_value) if size is near a boundary,
    else None.

    Uses KB minSize/maxSize.  Filters by location_group description keywords
    when provided so we only compare codes that actually apply at this location.
    """
    codes = kb.get_codes_by_name(pro_name)
    if not codes:
        return None

    # Primaries only, sorted ascending by max_size
    primaries = sorted(
        [c for c in codes if not c.is_addon and c.max_size > 0],   # is_addon uses addOn + parent_code
        key=lambda c: c.max_size,
    )

    # Optionally filter by location keywords in description
    if location_group:
        from services.code_selectors.base import match_desc_by_location
        loc_filtered = [
            c for c in primaries
            if any(
                kw in c.description.lower()
                for kw in _LOCATION_KEYWORDS.get(location_group, [])
            )
        ]
        if loc_filtered:
            primaries = sorted(loc_filtered, key=lambda c: c.max_size)

    for i, code in enumerate(primaries):
        boundary = code.max_size
        if abs(size - boundary) <= tolerance and abs(size - boundary) > _EPSILON:
            next_code = primaries[i + 1] if i + 1 < len(primaries) else None
            return (code.code, next_code.code if next_code else "next range", boundary)

    return None


_LOCATION_KEYWORDS: Dict[str, List[str]] = {
    "face":        ["face", "ear", "eyelid", "nose", "lip", "mucous"],
    "high_risk":   ["face", "neck", "hand", "foot", "axilla", "genitalia"],
    "special":     ["scalp", "neck", "hand", "foot", "genitalia"],
    "trunk":       ["trunk", "arm", "leg"],
    "extremities": ["scalp", "arm", "leg"],
    "critical":    ["nose", "lip", "ear", "eyelid"],
}


# ─────────────────────────────────────────────────────────────────────────────
# SUGGESTED RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def _suggested_code(
    pro_name: str,
    size: float,
    location_group: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Return the best deterministic code for a boundary-zone size.

    For sizes that fall exactly at a boundary (between two code ranges), return
    the code immediately ABOVE the boundary — the coder should verify whether
    the true measurement is above or below.
    """
    from services.code_selectors.base import (
        load_codes_by_name, match_by_size, match_desc_by_location,
    )

    codes = load_codes_by_name(pro_name)
    primaries = [c for c in codes if not c["associatedWithProCode"]]

    # Apply location filter when provided
    pool = match_desc_by_location(primaries, location_group) if location_group else primaries

    # 1. Try exact range match
    for c in sorted(pool, key=lambda x: float(x["minSize"])):
        if float(c["minSize"]) <= size <= float(c["maxSize"]) + _EPSILON:
            return {
                "code": c["code"],
                "description": c["description"],
                "size_range": f"{c['minSize']}–{c['maxSize']} cm",
                "confidence": "review",
                "note": (
                    f"Size {size}cm falls within range [{c['minSize']}, {c['maxSize']}] cm. "
                    f"Verify measurement is accurate — size is near a code boundary."
                ),
            }

    # 2. Size falls in a gap (between two ranges) — return the code just above
    above = sorted(
        [c for c in pool if float(c["minSize"]) > size],
        key=lambda x: float(x["minSize"]),
    )
    if above:
        c = above[0]
        return {
            "code": c["code"],
            "description": c["description"],
            "size_range": f"{c['minSize']}–{c['maxSize']} cm",
            "confidence": "review",
            "note": (
                f"Size {size}cm is at the boundary. If measurement confirms ≥{c['minSize']}cm, "
                f"use {c['code']}. If ≤{float(c['minSize']) - 0.1:.1f}cm, use the code below."
            ),
        }

    return None


# ─────────────────────────────────────────────────────────────────────────────
# PROCEDURE-SPECIFIC CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def _check(
    pro_name: str,
    size: Optional[float],
    description: str,
    location_group: Optional[str] = None,
    tolerance: float = _DEFAULT_TOLERANCE,
) -> Optional[Dict[str, Any]]:
    if size is None:
        return None
    boundary = _find_boundary(pro_name, size, location_group, tolerance)
    if not boundary:
        return None
    code_below, code_above, bval = boundary
    suggested = _suggested_code(pro_name, size, location_group)
    return {
        "description": (
            f"{description}: {size}cm is within {tolerance}cm of the "
            f"{bval}cm boundary between {code_below} (≤{bval}cm) "
            f"and {code_above} (>{bval}cm). Verify documentation supports "
            f"the selected code."
        ),
        "reason": "boundary_case",
        "suggested_resolution": suggested,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def detect_boundary_cases(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Scan all parsed sections for sizes near CPT code range boundaries.

    Returns a list of unresolved_procedure-format dicts that can be merged
    into parsed["unresolved_procedures"].  Each entry includes a
    `suggested_resolution` with the best deterministic code selection.

    Only checks sizes against boundaries for THEIR OWN procedure family —
    never cross-applies excision boundaries to closure sizes or vice versa.
    """
    issues: List[Dict] = []

    # ── Excision ─────────────────────────────────────────────────────────────
    for sec in parsed.get("excision_sections", []):
        size = sec.get("size")
        text = (sec.get("text") or "").lower()
        import re
        is_malignant = bool(re.search(r"(?<!non[- ])\bmalignant\b", text))
        pro_name = (
            "Excision Malignant Lesion & Margins"
            if is_malignant else
            "Excision Benign Lesion & Margins"
        )
        loc = sec.get("location") or ""
        from services.code_selectors.base import classify_location
        loc_group = classify_location(loc)
        issue = _check(
            pro_name, size,
            f"Excision ({'malignant' if is_malignant else 'benign'}) "
            f"at {loc or 'unspecified'}",
            loc_group,
        )
        if issue:
            issues.append(issue)

    # ── Shave Removal ─────────────────────────────────────────────────────────
    for sec in parsed.get("shave_removal_sections", []):
        size = sec.get("size")
        loc_group = sec.get("location_group") or "trunk"
        issue = _check(
            "Shave Removal", size,
            f"Shave removal ({loc_group})",
            loc_group,
        )
        if issue:
            issues.append(issue)

    # ── Complex Closure ───────────────────────────────────────────────────────
    for group in parsed.get("closure_aggregated", []):
        if (group.get("type") or "").lower() != "complex":
            continue
        size = group.get("total_size")
        loc_group = str(group.get("group_key") or "").split("_")[-1] or "trunk"
        issue = _check(
            "Complex Closure", size,
            f"Complex closure at {group.get('locations', ['unspecified'])[0]}",
            loc_group,
        )
        if issue:
            issues.append(issue)

    # ── Layered (Intermediate) Closure ────────────────────────────────────────
    for group in parsed.get("closure_aggregated", []):
        if (group.get("type") or "").lower() not in ("intermediate", "layered"):
            continue
        size = group.get("total_size")
        loc_group = str(group.get("group_key") or "").split("_")[-1] or "trunk"
        issue = _check(
            "Layered Closure", size,
            f"Layered closure at {group.get('locations', ['unspecified'])[0]}",
            loc_group,
        )
        if issue:
            issues.append(issue)

    if issues:
        logger.info(f"BoundaryChecker: {len(issues)} boundary case(s) detected")
    else:
        logger.debug("BoundaryChecker: no boundary cases")

    return issues
