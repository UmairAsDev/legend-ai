# services/mdm_classifier.py
"""
Medical Decision Making (MDM) complexity classifier.

Based on 2021 AMA E/M guidelines adapted for dermatology practice.

Uses a categorical tier system — not additive scoring — to avoid low-complexity
signals stacking into a false high-complexity result.

Tier evaluation order: HIGH → MODERATE → LOW → STRAIGHTFORWARD.
The first tier that matches wins.

Level 5 (high):    biologics / systemic immunosuppression / hospitalization
Level 4 (moderate): worsening / treatment failure / new systemic Rx / 3+ diagnoses
Level 3 (low):     any established follow-up with prescription management (DEFAULT)
Level 2 (SF):      resolved / single minor problem / OTC-only management
"""

import re
from typing import Any, Dict

# ---------------------------------------------------------------
# TIER PATTERNS
# ---------------------------------------------------------------

_HIGH_PATTERNS = [
    r"\bbiologic(?:s)?\b",
    r"\bdupilumab\b", r"\bsecukinumab\b", r"\bixekizumab\b",
    r"\bguselkumab\b", r"\brisankizumab\b", r"\bbrodalumab\b",
    r"\bmethotrexate\b", r"\bcyclosporine\b", r"\bmycophenolate\b",
    r"\bimmunosuppres\w+",
    r"\bhospitali[sz]\w+",
    r"\blife[- ]threatening\b",
    r"\bsepsis\b", r"\bcellulitis\b",
]

_MODERATE_WORSENING_PATTERNS = [
    r"\bworsening\b",
    r"\bflare\b",                           # NOT "flares" — word boundary enforced
    r"\bexacerb\w+\b",
    r"\buncontrolled\b",
    r"\bnot\s+(?:improving|controlled|responding)\b",
    r"\btreatment\s+failure\b",
    r"\bspreading\b",
    r"\bprogress(?:ing|ed|ion)\b",
]

_MODERATE_MANAGEMENT_PATTERNS = [
    r"\bnot\s+at\s+treatment\s+goal\b",
    r"\bswitch(?:ing|ed)\s+(?:to|medication|treatment)\b",
    r"\bchanging\s+(?:medication|treatment)\b",
    r"\bnew\s+(?:prescription|systemic|oral)\s+\w+\b",
    r"\bescalat\w+",
]

# Systemic prescription medications (any detected → at least level 3)
_SYSTEMIC_MEDS = [
    r"\bspironolactone\b", r"\bdoxycycline\b", r"\bminocycline\b",
    r"\bisotretinoin\b", r"\bdapsone\b", r"\bhydroxychloroquine\b",
    r"\bprednisone\b", r"\bprednisolone\b", r"\bcolchicine\b",
    r"\bplaquenil\b", r"\bsoriatane\b", r"\bacitretin\b",
    r"\btretinoin\b", r"\bclindamycin\s+oral\b",
]

# Resolved / minor signals — suggest straightforward
_RESOLVED_PATTERNS = [
    r"\bresolved\b", r"\bcleared?\b", r"\bno\s+concerns?\b",
    r"\bnormal\b", r"\bwithin\s+normal\b",
]


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _count_diagnoses(diagnoses_text: str) -> int:
    codes = re.findall(r"\b[A-Z]\d{2}\.?\d*\b", diagnoses_text or "")
    return len(set(codes))


def classify_mdm_level(note: Dict[str, Any]) -> int:
    """
    Return MDM complexity level 2-5.

    Priority order: HIGH → MODERATE → LOW/default → STRAIGHTFORWARD.
    """
    corpus = " ".join([
        note.get("complaints") or "",
        note.get("assesment") or "",
        note.get("currentmedication") or "",
        note.get("procedure") or "",
        note.get("reviewofsystem") or "",
    ])

    dx_count = _count_diagnoses(note.get("diagnoses") or "")
    has_systemic = _any_match(_SYSTEMIC_MEDS, corpus)

    # ---- LEVEL 5: high complexity ----
    if _any_match(_HIGH_PATTERNS, corpus):
        return 5

    # ---- LEVEL 4: moderate complexity ----
    # Worsening/treatment failure OR active management change OR 3+ diagnoses
    if (
        _any_match(_MODERATE_WORSENING_PATTERNS, corpus)
        or _any_match(_MODERATE_MANAGEMENT_PATTERNS, corpus)
        or dx_count >= 3
    ):
        return 4

    # ---- LEVEL 2 check: resolved condition overrides follow-up signal ----
    # If the assessment documents resolution, the note is straightforward
    # regardless of whether it was framed as a follow-up visit.
    if _any_match(_RESOLVED_PATTERNS, corpus) and not has_systemic:
        return 2

    # ---- LEVEL 3: low complexity (default for most dermatology follow-ups) ----
    if has_systemic:
        return 3

    is_followup = bool(re.search(r"\bfollow[- ]?up\b|\bf/u\b", corpus, re.IGNORECASE))
    if is_followup:
        return 3

    # ---- LEVEL 2: straightforward (no follow-up, no systemic, no signals) ----

    # Default for an established-patient office visit with no other signals
    return 3


def extract_diagnoses_from_note(note: Dict[str, Any]) -> list[str]:
    """Parse ICD-10 codes from the diagnoses field. Returns e.g. ['L70.0', 'L71.9']."""
    raw = note.get("diagnoses") or ""
    codes = re.findall(r"\b([A-Z]\d{2}\.?\d*)\b", raw)
    return list(dict.fromkeys(codes))
