# config/constants.py
"""
Operational constants that are NOT derivable from proCodeList.csv.

Billing thresholds (quantity ranges, size ranges) live in the CSV
via minQty/maxQty/minSize/maxSize — those are NOT here.
Only values the CSV cannot provide belong in this file.
"""

# ─────────────────────────────────────────────────────────────
# SRT — medical physics parameter, not a billing quantity
# ─────────────────────────────────────────────────────────────

# kV at or below this threshold → superficial delivery code (77437)
# Above this threshold → orthovoltage delivery code (77438)
SRT_KV_BOUNDARY = 150

# ─────────────────────────────────────────────────────────────
# PIPELINE LIMITS
# ─────────────────────────────────────────────────────────────

MAX_CANDIDATES_FOR_LLM    = 15   # Max ambiguous candidate codes sent to LLM
MAX_WEB_SEARCHES_PER_NOTE = 2    # Max web search calls per note
WEB_SEARCH_MAX_CHARS      = 600  # Characters kept from each search result
MIN_METHOD_TOKEN_LENGTH   = 3    # Min chars for a method keyword to be meaningful

# ─────────────────────────────────────────────────────────────
# CPT BOUNDARY FLAGGING
# ─────────────────────────────────────────────────────────────

# Flag a size if it falls within this many cm of a CPT code boundary
CPT_BOUNDARY_TOLERANCE = 0.3

# Excision size boundaries listed for web-search trigger logic only.
# Code selection itself reads minSize/maxSize directly from the database.
EXCISION_BOUNDARIES = [0.5, 1.0, 2.0, 3.0, 4.0]

# Last-resort fallback step for closure add-on unit calculation when the
# code description does not state the increment and minSize is also missing.
CLOSURE_ADDON_DEFAULT_STEP = 5.0

# ─────────────────────────────────────────────────────────────
# CODE STRUCTURE — prefix rules for code-type identification
# ─────────────────────────────────────────────────────────────

CLOSURE_CODE_PREFIXES  = ("120", "131", "140")
EXCISION_CODE_PREFIXES = ("114", "116")

# ─────────────────────────────────────────────────────────────
# CCI BUNDLED PAIRS
# Source: CMS National Correct Coding Initiative edits.
# Secondary code (key) bundles with any of its primary codes (value set)
# when performed on the same single lesion in the same session.
# Update quarterly when CMS releases a new CCI table.
# ─────────────────────────────────────────────────────────────

BUNDLED_PAIRS = {
    "11310": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},
    "11311": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},
    "11312": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},
    "11313": {"11100", "11101", "11102", "11103", "11104", "11105", "11106", "11107"},
}
