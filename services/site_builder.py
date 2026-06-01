# services/site_builder.py
"""
Phase 1 — Site-Centric Data Model.

Converts the flat parsed-section dict produced by billing_params into a list
of ProcedureSite objects.  Every procedure must belong to a site.

A "site" is a single anatomical location (lesion or area) where one or more
procedures are performed.  Grouping is determined by:

  1. Section label  (A, B, C ...) — when present, sections with the same label
     at the same anatomical site are the same site.  This is the primary key
     because clinical notes routinely label each lesion with a letter.

  2. Normalised location text — fallback when no label is present.

  3. Each un-labelled, un-located section gets its own unique site.

The builder also back-annotates each parsed section in-place with its
`site_id` so that selectors and the modifier engine can trace every CPT code
back to the site that produced it without a separate lookup.

Downstream consumers:
  - services/modifier_engine.py   (Phase 8)  — site-aware -59 assignment
  - services/lesion_validator.py  (Phase 5)  — same-site conflict detection
  - services/validation_engine.py (Phase 4)  — site-level validation rules
  - services/confidence_engine.py (Phase 12) — per-site scoring
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from services.code_selectors.base import classify_location, classify_closure_location


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProcedureItem:
    """One procedure performed at a site."""
    procedure_type: str          # "biopsy" | "excision" | "destruction_dpm" | ...
    section: Dict[str, Any]      # the raw parsed section dict (already annotated with site_id)
    cpt_codes: List[Dict] = field(default_factory=list)  # filled after selection


@dataclass
class ProcedureSite:
    """
    A single anatomical site in the clinical note.

    Groups all procedures at the same location so validators can detect
    conflicts (biopsy + shave same lesion) and the modifier engine can
    determine distinct-site status for modifier -59.
    """
    site_id: str
    location: str                        # free-text from the note
    location_group: str                  # "face" | "special" | "trunk" | closure variant
    diagnosis_codes: List[str] = field(default_factory=list)  # populated post-coding
    lesion_count: int = 1
    procedures: List[ProcedureItem] = field(default_factory=list)

    # ── Convenience ──────────────────────────────────────────────────────────

    def add_procedure(self, procedure_type: str, section: Dict[str, Any]) -> ProcedureItem:
        item = ProcedureItem(procedure_type=procedure_type, section=section)
        self.procedures.append(item)
        return item

    @property
    def procedure_types(self) -> List[str]:
        return [p.procedure_type for p in self.procedures]

    def has_procedure(self, procedure_type: str) -> bool:
        return procedure_type in self.procedure_types

    def to_dict(self) -> Dict[str, Any]:
        return {
            "site_id":        self.site_id,
            "location":       self.location,
            "location_group": self.location_group,
            "diagnosis_codes": self.diagnosis_codes,
            "lesion_count":   self.lesion_count,
            "procedure_types": self.procedure_types,
        }


# ─────────────────────────────────────────────────────────────────────────────
# SECTION-TYPE REGISTRY
# Maps parsed section key → (default procedure_type, location field name)
# ─────────────────────────────────────────────────────────────────────────────

_SECTION_REGISTRY: List[Tuple[str, Optional[str], str]] = [
    # (section_key, procedure_type or None, location_field)
    # procedure_type=None means derive from section content (e.g. destruction type)
    ("biopsy_sections",              "biopsy",          "location"),
    ("excision_sections",            "excision",        "location"),
    ("shave_removal_sections",       "shave_removal",   "location"),
    ("destruction_sections",         None,              "destruction_location"),
    ("mohs_sections",                "mohs",            "location"),
    ("closure_sections",             "closure",         "location"),
    ("srt_sections",                 "srt",             "location"),
    ("debridement_sections",         "debridement",     "location"),
    ("xtrac_sections",               "xtrac",           "location"),
    ("ipl_sections",                 "ipl",             "location"),
    ("laser_treatment_sections",     "laser_treatment", "location"),
    ("filler_sections",              "filler",          "location"),
    ("filler_material_sections",     "filler_material", "location"),
    ("chemical_peel_sections",       "chemical_peel",   "location"),
]


# ─────────────────────────────────────────────────────────────────────────────
# SITE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_sites(parsed: Dict[str, Any]) -> List[ProcedureSite]:
    """
    Group all parsed procedure sections into ProcedureSite objects.

    Side effect: annotates every section dict in `parsed` in-place with
    a `site_id` key so that downstream selector calls can tag produced
    CPT codes with the site they belong to.

    Returns a list of ProcedureSite objects (one per distinct site).
    """
    sites_by_key: Dict[str, ProcedureSite] = {}
    site_counter = 0

    for section_key, default_type, loc_field in _SECTION_REGISTRY:
        sections = parsed.get(section_key) or []

        for sec in sections:
            location = str(sec.get(loc_field) or sec.get("location") or "").strip()
            label    = str(sec.get("label")  or "").strip().upper()

            group_key = _group_key(label, location, section_key, id(sec))

            if group_key not in sites_by_key:
                location_group = _classify(location, section_key)
                site_id = f"site_{site_counter}"
                site_counter += 1
                sites_by_key[group_key] = ProcedureSite(
                    site_id=site_id,
                    location=location,
                    location_group=location_group,
                    lesion_count=_lesion_count(sec),
                )

            site = sites_by_key[group_key]

            # Derive procedure type
            proc_type = default_type
            if proc_type is None:
                dtype = str(sec.get("destruction_type") or "").lower()
                proc_type = f"destruction_{dtype}" if dtype else "destruction_unknown"

            site.add_procedure(proc_type, sec)

            # Back-annotate the section in-place so selectors can tag codes
            sec["site_id"] = site.site_id

    sites = list(sites_by_key.values())

    logger.info(f"SiteBuilder: {len(sites)} site(s) identified")
    for s in sites:
        logger.debug(
            f"  {s.site_id}: loc={s.location!r:30s} "
            f"group={s.location_group:12s} "
            f"procedures={s.procedure_types}"
        )

    return sites


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _group_key(label: str, location: str, section_key: str, obj_id: int) -> str:
    """
    Determine the site grouping key for a section.

    Priority:
      1. Section label  (A, B, C …) — most reliable grouping signal
      2. Normalised location text   — fallback for unlabelled sections
      3. Object identity             — each section gets its own site when
                                       neither label nor location is present
    """
    if label:
        return f"label:{label}"
    if location:
        return f"loc:{location.lower()}"
    return f"unique:{section_key}:{obj_id}"


def _classify(location: str, section_key: str) -> str:
    """Map location text to the location group appropriate for this section type."""
    if not location:
        return "trunk"
    if section_key == "closure_sections":
        return classify_closure_location(location)
    return classify_location(location)


def _lesion_count(sec: Dict[str, Any]) -> int:
    """Extract lesion/lesion-count from a section dict."""
    for field in ("lesion_count", "quantity", "count"):
        val = sec.get(field)
        if val is not None:
            try:
                return max(1, int(val))
            except (ValueError, TypeError):
                pass
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# SITE LOOKUP HELPERS  (used by modifier engine and validators)
# ─────────────────────────────────────────────────────────────────────────────

def site_for_code(code_dict: Dict[str, Any], sites: List[ProcedureSite]) -> Optional[ProcedureSite]:
    """
    Return the ProcedureSite that owns this CPT code dict.

    Matches on the `site_id` field that selectors attach to each code when
    they call make_code() with the annotated section.  Falls back to a
    source-type scan if site_id is absent (e.g. LLM-assigned codes).
    """
    # Primary: direct site_id tag
    site_id = code_dict.get("site_id", "")
    if site_id:
        for s in sites:
            if s.site_id == site_id:
                return s

    # Fallback: match by source procedure type
    source = code_dict.get("source", "")
    if source:
        for s in sites:
            if any(source.startswith(p.procedure_type) for p in s.procedures):
                return s

    return None


def same_site(code_a: Dict, code_b: Dict, sites: List[ProcedureSite]) -> bool:
    """True when both codes belong to the same ProcedureSite."""
    a_site = site_for_code(code_a, sites)
    b_site = site_for_code(code_b, sites)
    if a_site is None or b_site is None:
        return False
    return a_site.site_id == b_site.site_id
