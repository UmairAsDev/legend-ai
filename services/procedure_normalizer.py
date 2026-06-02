# services/procedure_normalizer.py
"""
Procedure Normalizer — converts the raw parsed section dict into a list of
standardized ProcedureInstance objects.

The clinical parser produces type-specific section lists:
    parsed["biopsy_sections"]    = [{label, location, quantity, text, ...}]
    parsed["excision_sections"]  = [{label, size, location, lesion_type, ...}]
    ...

This normalizer reads every section list and converts each entry into a
ProcedureInstance with a canonical family, type, location, location_group,
size, and quantity.

The output is a flat list of ProcedureInstance objects that flows into:
  - site_builder  → groups into ProcedureSite objects
  - selectors     → each selector accepts ProcedureInstance
  - validators    → compare instances across sites

Type detection uses the section's existing fields (destruction_type,
lesion_type, etc.) and keyword scanning of the section text when
structured fields are absent.  The family is deterministic; the type
may be "unknown" when the note doesn't document the required detail.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from loguru import logger

from services.code_selectors.base import classify_location, classify_closure_location
from services.procedure_models import (
    AttType, BiopsyType, ClosureType, CryoType, DestructionType,
    EdcType, ExcisionType, Family, FillerType, IDType, InjectionType,
    LocationGroup, MohsType, NailType, PeelType, ProcedureInstance,
    ShaveType, SkinGraftType,
)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def normalize_procedures(parsed: Dict[str, Any]) -> List[ProcedureInstance]:
    """
    Convert all parsed section lists into a flat list of ProcedureInstance objects.

    Called after billing_params has produced the merged parsed dict.
    The result is stored in state["procedures"] and passed to the site builder.
    """
    instances: List[ProcedureInstance] = []

    instances.extend(_normalize_biopsies(parsed))
    instances.extend(_normalize_shave_removals(parsed))
    instances.extend(_normalize_excisions(parsed))
    instances.extend(_normalize_mohs(parsed))
    instances.extend(_normalize_simple_closures(parsed))
    instances.extend(_normalize_layered_closures(parsed))
    instances.extend(_normalize_complex_closures(parsed))
    instances.extend(_normalize_adjacent_transfers(parsed))
    instances.extend(_normalize_destructions(parsed))
    instances.extend(_normalize_debridements(parsed))
    instances.extend(_normalize_injections(parsed))
    instances.extend(_normalize_incision_drainage(parsed))
    instances.extend(_normalize_nail_procedures(parsed))
    instances.extend(_normalize_srt(parsed))
    instances.extend(_normalize_xtrac(parsed))
    instances.extend(_normalize_ipl(parsed))
    instances.extend(_normalize_chemical_peels(parsed))
    instances.extend(_normalize_fillers(parsed))
    instances.extend(_normalize_skin_tags(parsed))

    logger.info(
        f"ProcedureNormalizer: {len(instances)} procedure instance(s) — "
        + ", ".join(f"{f}: {sum(1 for i in instances if i.family==f)}"
                   for f in sorted({i.family for i in instances}))
    )
    return instances


# ─────────────────────────────────────────────────────────────────────────────
# PER-FAMILY NORMALIZERS
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_biopsies(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("biopsy_sections", []):
        btype = _biopsy_type(sec.get("text") or "")
        loc   = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.BIOPSY,
            type=btype,
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_shave_removals(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("shave_removal_sections", []):
        loc_group = str(sec.get("location_group") or "")
        loc       = str(sec.get("location") or "")
        if not loc_group:
            loc_group = _shave_location_group(classify_location(loc))
        instances.append(ProcedureInstance(
            family=Family.SHAVE_REMOVAL,
            type=loc_group or ShaveType.TRUNK,
            location=loc,
            location_group=loc_group or LocationGroup.TRUNK,
            size=sec.get("size"),
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_excisions(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("excision_sections", []):
        text = (sec.get("text") or "").lower()
        etype = (
            ExcisionType.MALIGNANT
            if re.search(r"(?<!non[- ])\bmalignant\b", text)
            else ExcisionType.BENIGN
        )
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.EXCISION,
            type=etype,
            location=loc,
            location_group=classify_location(loc),
            size=sec.get("size"),
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_mohs(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("mohs_sections", []):
        loc    = str(sec.get("location") or "")
        stages = int(sec.get("stages") or 1)
        loc_risk = _mohs_risk(loc)

        # First stage — primary code
        instances.append(ProcedureInstance(
            family=Family.MOHS,
            type=MohsType.FIRST_STAGE,
            location=loc,
            location_group=loc_risk,
            size=None,
            quantity=1,
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
        # Additional stages — add-on
        if stages > 1:
            instances.append(ProcedureInstance(
                family=Family.MOHS,
                type=MohsType.ADDITIONAL_STAGE,
                location=loc,
                location_group=loc_risk,
                size=None,
                quantity=stages - 1,
                label=str(sec.get("label") or ""),
                raw_section=sec,
            ))
    return instances


def _normalize_simple_closures(parsed: Dict) -> List[ProcedureInstance]:
    return _normalize_closures(parsed, "simple", Family.SIMPLE_CLOSURE)


def _normalize_layered_closures(parsed: Dict) -> List[ProcedureInstance]:
    return _normalize_closures(parsed, "intermediate", Family.INTERMEDIATE_CLOSURE)


def _normalize_complex_closures(parsed: Dict) -> List[ProcedureInstance]:
    return _normalize_closures(parsed, "complex", Family.COMPLEX_CLOSURE)


def _normalize_closures(
    parsed: Dict, closure_type: str, family: str
) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("closure_sections", []):
        if (sec.get("type") or "").lower() != closure_type:
            continue
        loc       = str(sec.get("location") or "")
        loc_group = str(sec.get("location_group") or "") or classify_closure_location(loc)
        instances.append(ProcedureInstance(
            family=family,
            type=loc_group or LocationGroup.TRUNK,
            location=loc,
            location_group=loc_group or LocationGroup.TRUNK,
            size=sec.get("size"),
            quantity=1,
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_adjacent_transfers(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("closure_sections", []):
        if (sec.get("type") or "").lower() != "adjacent":
            continue
        loc       = str(sec.get("location") or "")
        loc_group = str(sec.get("location_group") or "") or classify_closure_location(loc)
        text      = (sec.get("text") or "").lower()
        att_type  = _att_type(text)
        instances.append(ProcedureInstance(
            family=Family.ADJACENT_TRANSFER,
            type=att_type,
            location=loc,
            location_group=loc_group or LocationGroup.TRUNK,
            size=sec.get("size"),
            quantity=1,
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_destructions(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("destruction_sections", []):
        dtype = (sec.get("destruction_type") or "").lower()
        dtype_mapped = _destruction_type(dtype, sec.get("text") or "")
        loc = str(sec.get("location") or sec.get("destruction_location") or "")
        instances.append(ProcedureInstance(
            family=Family.DESTRUCTION,
            type=dtype_mapped,
            location=loc,
            location_group=classify_location(loc),
            size=sec.get("size"),
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_debridements(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("debridement_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.DEBRIDEMENT,
            type=_debridement_type(sec),
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_injections(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("ipl_sections", []):   # IPL handled separately
        pass
    for sec in parsed.get("filler_sections", []):  # Filler handled separately
        pass
    # Intralesional injection — if the parser surfaces it as a separate section
    for sec in parsed.get("injection_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.INTRALESIONAL_INJ,
            type=_injection_type(sec.get("text") or ""),
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_incision_drainage(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("incision_drainage_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.INCISION_DRAINAGE,
            type=_id_type(sec.get("text") or ""),
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_nail_procedures(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("nail_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.NAIL,
            type=_nail_type(sec.get("text") or ""),
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_srt(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("srt_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.SRT,
            type="superficial" if (sec.get("kv") or 0) <= 150 else "orthovoltage",
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_xtrac(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("xtrac_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.XTRAC,
            type="xtrac",
            location=loc,
            location_group=classify_location(loc),
            size=sec.get("total_area"),
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_ipl(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("ipl_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.IPL,
            type=str(sec.get("method") or "unspecified"),
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_chemical_peels(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("chemical_peel_sections", []):
        loc  = str(sec.get("location") or "")
        ptype = (
            PeelType.DERMAL if (sec.get("choice") or "").lower() == "dermal"
            else PeelType.EPIDERMAL
        )
        instances.append(ProcedureInstance(
            family=Family.CHEMICAL_PEEL,
            type=ptype,
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_fillers(parsed: Dict) -> List[ProcedureInstance]:
    instances = []
    for sec in parsed.get("filler_material_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.FILLER,
            type=FillerType.MATERIAL,
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    for sec in parsed.get("filler_sections", []):
        loc = str(sec.get("location") or "")
        instances.append(ProcedureInstance(
            family=Family.FILLER,
            type=FillerType.INJECTION,
            location=loc,
            location_group=classify_location(loc),
            size=None,
            quantity=int(sec.get("quantity") or 1),
            label=str(sec.get("label") or ""),
            raw_section=sec,
        ))
    return instances


def _normalize_skin_tags(parsed: Dict) -> List[ProcedureInstance]:
    # Skin tags may appear in destruction_sections with type "skin_tag"
    # or as a separate family — detect via raw_section type field
    instances = []
    for sec in parsed.get("destruction_sections", []):
        text = (sec.get("text") or "").lower()
        if "skin tag" in text or "acrochordon" in text:
            loc = str(sec.get("location") or sec.get("destruction_location") or "")
            instances.append(ProcedureInstance(
                family=Family.SKIN_TAG,
                type="removal",
                location=loc,
                location_group=classify_location(loc),
                size=None,
                quantity=int(sec.get("quantity") or 1),
                label=str(sec.get("label") or ""),
                raw_section=sec,
            ))
    return instances


# ─────────────────────────────────────────────────────────────────────────────
# TYPE DETECTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _biopsy_type(text: str) -> str:
    t = text.lower()
    if "punch" in t:
        return BiopsyType.PUNCH
    if any(w in t for w in ("tangential", "shave", "saucerize", "scoop")):
        return BiopsyType.TANGENTIAL
    if "incisional" in t:
        return BiopsyType.INCISIONAL
    if "excisional" in t:
        return BiopsyType.EXCISIONAL
    return BiopsyType.UNKNOWN


def _shave_location_group(classify_result: str) -> str:
    """Map a classify_location result to a ShaveType constant."""
    return {
        "face":    ShaveType.FACE,
        "special": ShaveType.SPECIAL,
        "trunk":   ShaveType.TRUNK,
    }.get(classify_result, ShaveType.TRUNK)


def _mohs_risk(location: str) -> str:
    """Classify Mohs location as head_neck (high risk) or trunk_extremity."""
    _HIGH = {
        "head", "neck", "face", "scalp", "ear", "nose", "lip", "temple",
        "eyelid", "jaw", "cheek", "chin", "forehead", "periorbital",
        "perinasal", "perioral",
    }
    tokens = set(location.lower().split())
    return LocationGroup.HEAD_NECK if tokens & _HIGH else LocationGroup.TRUNK_EXTREMITY


def _att_type(text: str) -> str:
    t = text.lower()
    if "advancement" in t or "advanced" in t:
        return AttType.ADVANCEMENT
    if "rotation" in t:
        return AttType.ROTATION
    if "transposition" in t:
        return AttType.TRANSPOSITION
    if "island" in t:
        return AttType.ISLAND
    if "pedicle" in t:
        return AttType.PEDICLE
    if "banner" in t:
        return AttType.BANNER
    if "rhombic" in t:
        return AttType.RHOMBIC
    if "bilobed" in t:
        return AttType.BILOBED
    return AttType.UNKNOWN


def _destruction_type(dtype: str, text: str) -> str:
    """Map parsed destruction_type field + text keywords to a canonical DestructionType."""
    if dtype == "dpm":
        return DestructionType.PREMALIGNANT
    if dtype in ("db", "dbm"):
        return DestructionType.BENIGN
    if dtype == "dm":
        return DestructionType.MALIGNANT
    # Keyword fallback when dtype field is absent
    t = text.lower()
    if any(w in t for w in ("actinic", "keratosis", "ak", "premalignant")):
        return DestructionType.PREMALIGNANT
    if any(w in t for w in ("malignant", "carcinoma", "melanoma")):
        return DestructionType.MALIGNANT
    if any(w in t for w in ("seborrheic", "sk", "wart", "verruca", "molluscum", "condyloma")):
        return DestructionType.BENIGN
    return DestructionType.BENIGN  # default in dermatology


def _debridement_type(sec: Dict) -> str:
    if sec.get("nail"):
        return "nail"
    if sec.get("is_wound"):
        return "wound"
    if sec.get("dermatologic"):
        return "dermatologic"
    return "unspecified"


def _injection_type(text: str) -> str:
    t = text.lower()
    if "keloid" in t:
        return InjectionType.KELOID
    if "hypertrophic" in t:
        return InjectionType.HYPERTROPHIC_SCAR
    if "alopecia" in t:
        return InjectionType.ALOPECIA_AREATA
    if "cyst" in t:
        return InjectionType.CYST
    return InjectionType.UNSPECIFIED


def _id_type(text: str) -> str:
    t = text.lower()
    if "complicated" in t or "complex" in t:
        return IDType.COMPLICATED
    if "abscess" in t:
        return IDType.ABSCESS
    if "cyst" in t:
        return IDType.CYST
    return IDType.SIMPLE


def _nail_type(text: str) -> str:
    t = text.lower()
    if "matrixectomy" in t or "matrix" in t:
        return NailType.MATRIXECTOMY
    if "wedge" in t:
        return NailType.WEDGE
    if "partial" in t:
        return NailType.PARTIAL
    return NailType.AVULSION
