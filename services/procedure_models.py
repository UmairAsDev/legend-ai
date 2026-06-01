# services/procedure_models.py
"""
Standardized procedure data model.

Every procedure extracted from a clinical note is represented as a
ProcedureInstance.  This is the single schema that flows between:
  - clinical parser  (produces ProcedureInstance objects)
  - site builder     (groups them into ProcedureSite)
  - selectors        (receive ProcedureInstance, return CPT codes)
  - validators       (check ProcedureInstance pairs for conflicts)

The standardized output of clinical extraction looks like:
    {
        "family": "adjacent_transfer",
        "type": "advancement_flap",
        "location": "right post lateral neck",
        "location_group": "high_risk",
        "size": 6.5,
        "quantity": 1,
        "diagnosis": ["C44.42"]
    }

No CPT code numbers appear in this model.  Code selection is entirely
the selector's responsibility, driven by family + type + size + location.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# FAMILY CONSTANTS
# These are the top-level procedure categories supported by the system.
# They map to one or more proName values in proCodeList.csv.
# Defined here as constants to avoid typo-driven bugs.
# ─────────────────────────────────────────────────────────────────────────────

class Family:
    BIOPSY               = "biopsy"
    SHAVE_REMOVAL        = "shave_removal"
    EXCISION             = "excision"
    MOHS                 = "mohs"
    SIMPLE_CLOSURE       = "simple_closure"
    INTERMEDIATE_CLOSURE = "intermediate_closure"
    COMPLEX_CLOSURE      = "complex_closure"
    ADJACENT_TRANSFER    = "adjacent_transfer"
    SKIN_GRAFT           = "skin_graft"
    DESTRUCTION          = "destruction"
    EDC                  = "edc"
    CRYOTHERAPY          = "cryotherapy"
    INTRALESIONAL_INJ    = "intralesional_injection"
    INCISION_DRAINAGE    = "incision_drainage"
    NAIL                 = "nail"
    SKIN_TAG             = "skin_tag"
    PDT                  = "pdt"
    SRT                  = "srt"
    XTRAC                = "xtrac"
    IPL                  = "ipl"
    CHEMICAL_PEEL        = "chemical_peel"
    FILLER               = "filler"
    BOTOX                = "botox"
    LASER                = "laser"
    DEBRIDEMENT          = "debridement"


# ─────────────────────────────────────────────────────────────────────────────
# TYPE CONSTANTS (per family)
# ─────────────────────────────────────────────────────────────────────────────

class BiopsyType:
    TANGENTIAL  = "tangential_biopsy"   # shave/saucerize — 11102 series
    PUNCH       = "punch_biopsy"        # punch — 11104 series
    INCISIONAL  = "incisional_biopsy"   # incisional — 11106 series
    EXCISIONAL  = "excisional_biopsy"   # excisional
    UNKNOWN     = "unknown"             # method not documented


class ShaveType:
    FACE        = "face"                # face/ears/eyelids/nose/lips/mucous
    TRUNK       = "trunk"               # trunk/arms/legs (default)
    SPECIAL     = "special"             # scalp/neck/hands/feet/genitalia


class ExcisionType:
    BENIGN      = "benign"
    MALIGNANT   = "malignant"


class MohsType:
    FIRST_STAGE      = "first_stage"
    ADDITIONAL_STAGE = "additional_stage"    # add-on per additional stage


class ClosureType:
    FACE        = "face"
    NECK        = "neck"
    TRUNK       = "trunk"
    EXTREMITY   = "extremity"
    SCALP       = "scalp"
    HAND        = "hand"
    FOOT        = "foot"
    GENITALIA   = "genitalia"


class AttType:
    ADVANCEMENT     = "advancement_flap"
    ROTATION        = "rotation_flap"
    TRANSPOSITION   = "transposition_flap"
    PEDICLE         = "pedicle_flap"
    ISLAND          = "island_flap"
    BANNER          = "banner_flap"
    RHOMBIC         = "rhombic_flap"
    BILOBED         = "bilobed_flap"
    UNKNOWN         = "unspecified"


class SkinGraftType:
    SPLIT_THICKNESS = "split_thickness"
    FULL_THICKNESS  = "full_thickness"
    PINCH           = "pinch_graft"
    SUBSTITUTE      = "tissue_substitute"


class DestructionType:
    PREMALIGNANT         = "premalignant"        # DPM — actinic keratosis
    BENIGN               = "benign"              # DBM
    MALIGNANT            = "malignant"           # DM
    ACTINIC_KERATOSIS    = "actinic_keratosis"   # → DPM
    SEBORRHEIC_KERATOSIS = "seborrheic_keratosis" # → DBM
    WART                 = "wart"                # → DBM
    MOLLUSCUM            = "molluscum"           # → DBM
    CONDYLOMA            = "condyloma"           # → DBM
    VASCULAR             = "vascular"            # DVP


class EdcType:
    BCC              = "bcc"
    SCC              = "scc"
    SUPERFICIAL_BCC  = "superficial_bcc"
    LOW_RISK         = "low_risk"


class CryoType:
    ACTINIC_KERATOSIS    = "actinic_keratosis"
    WART                 = "wart"
    SEBORRHEIC_KERATOSIS = "seborrheic_keratosis"
    SKIN_TAG             = "skin_tag"


class InjectionType:
    KELOID              = "keloid"
    HYPERTROPHIC_SCAR   = "hypertrophic_scar"
    ALOPECIA_AREATA     = "alopecia_areata"
    CYST                = "cyst"
    INFLAMMATORY        = "inflammatory_lesion"
    UNSPECIFIED         = "unspecified"


class IDType:
    SIMPLE      = "simple"
    COMPLICATED = "complicated"
    ABSCESS     = "abscess"
    CYST        = "cyst"


class NailType:
    AVULSION        = "avulsion"
    PARTIAL         = "partial_avulsion"
    MATRIXECTOMY    = "matrixectomy"
    WEDGE           = "wedge_excision"


class PeelType:
    EPIDERMAL   = "epidermal"
    DERMAL      = "dermal"


class FillerType:
    MATERIAL    = "filler_material"    # tissue-filler material (11950-11954)
    INJECTION   = "filler_injection"   # filler injection service


# ─────────────────────────────────────────────────────────────────────────────
# LOCATION GROUPS
# ─────────────────────────────────────────────────────────────────────────────

class LocationGroup:
    FACE        = "face"
    SPECIAL     = "special"
    TRUNK       = "trunk"
    CRITICAL    = "critical"
    HIGH_RISK   = "high_risk"
    EXTREMITIES = "extremities"
    HEAD_NECK   = "head_neck"
    TRUNK_EXTREMITY = "trunk_extremity"


# ─────────────────────────────────────────────────────────────────────────────
# PROCEDURE INSTANCE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProcedureInstance:
    """
    Standardized representation of a single procedure from a clinical note.

    Produced by the clinical parser / normalizer.
    Consumed by the site builder, selectors, and validators.

    family and type use the constants above.
    No CPT codes are stored here — code assignment is the selector's job.
    """

    # Core identification
    family:         str                  # Family constant, e.g. Family.BIOPSY
    type:           str                  # Type constant within the family

    # Anatomical context
    location:       str                  # free-text from note: "left cheek"
    location_group: str                  # classified: LocationGroup.FACE / TRUNK / etc.

    # Billing parameters
    size:           Optional[float] = None   # cm (linear) or cm² (area)
    quantity:       int = 1
    diagnosis:      List[str] = field(default_factory=list)   # ICD-10 codes

    # Pipeline tracking
    site_id:        str = ""             # assigned by site builder
    label:          str = ""            # section letter from note (A, B, C…)

    # Original parsed section — preserved for reasoning and audit
    raw_section:    Dict[str, Any] = field(default_factory=dict)

    # ── Convenience ──────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family":         self.family,
            "type":           self.type,
            "location":       self.location,
            "location_group": self.location_group,
            "size":           self.size,
            "quantity":       self.quantity,
            "diagnosis":      self.diagnosis,
            "site_id":        self.site_id,
            "label":          self.label,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProcedureInstance":
        return cls(
            family=d.get("family", ""),
            type=d.get("type", ""),
            location=d.get("location", ""),
            location_group=d.get("location_group", ""),
            size=d.get("size"),
            quantity=int(d.get("quantity") or 1),
            diagnosis=d.get("diagnosis") or [],
            site_id=d.get("site_id", ""),
            label=d.get("label", ""),
        )

    def __repr__(self) -> str:
        return (
            f"ProcedureInstance("
            f"family={self.family!r}, type={self.type!r}, "
            f"loc={self.location!r}, size={self.size}, qty={self.quantity})"
        )
