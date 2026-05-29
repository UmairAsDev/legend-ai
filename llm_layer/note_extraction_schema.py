# llm_layer/note_extraction_schema.py
"""
Pydantic schemas for LLM-based procedure extraction (CoT Step 2).

All measurement fields are Optional — the LLM must never infer or estimate
values not explicitly documented in the note.  Missing required billing
fields go into unresolved_procedures, not as guessed values.

These schemas mirror the dict structure that ClinicalParser.parse() returns
so the merge function can combine both sources without touching downstream
selectors.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────
# PROCEDURE SECTION SCHEMAS
# ─────────────────────────────────────────────────────────────

class ExcisionSection(BaseModel):
    label: Optional[str] = None
    size: Optional[float] = Field(
        None,
        description="Max dimension in cm. Use excision size > wound size > closure size. Never use lesion size alone. Null if not documented."
    )
    location: Optional[str] = None
    lesion_type: Optional[str] = Field(
        None,
        description="'malignant' or 'benign'. Null if not stated."
    )
    quantity: int = Field(1, description="Number of lesions excised.")
    text: str = Field("", description="Verbatim excerpt from note relevant to this excision.")
    source: str = "llm"


class BiopsySection(BaseModel):
    label: Optional[str] = Field(None, description="Section label e.g. 'A', 'B'.")
    location: Optional[str] = None
    quantity: int = Field(1, description="Count as 1 per labeled site.")
    text: str = Field(
        "",
        description="Verbatim excerpt. MUST include the biopsy method word if known: 'punch', 'tangential', 'shave', or 'incisional'."
    )
    source: str = "llm"


class DestructionSection(BaseModel):
    label: Optional[str] = None
    destruction_type: Optional[str] = Field(
        None,
        description="'db' (benign lesion), 'dpm' (premalignant/actinic keratosis), or 'dm' (malignant). Null if not determinable."
    )
    location: Optional[str] = None
    quantity: Optional[int] = Field(None, description="Number of lesions destroyed. Null if not stated.")
    method: Optional[str] = None
    size: Optional[float] = Field(
        None,
        description="Max dimension in cm. Only relevant for dm (malignant). Null if not stated."
    )
    text: str = ""
    source: str = "llm"


class ShaveRemovalSection(BaseModel):
    label: Optional[str] = None
    location: Optional[str] = None
    location_group: Optional[str] = Field(
        None,
        description="'face' (face/ears/eyelids/nose/lips/mucous membrane), 'trunk' (trunk/arms/legs), or 'special' (scalp/neck/hands/feet/genitalia)."
    )
    size: Optional[float] = Field(
        None,
        description="Max dimension in cm. Use excision size first, then lesion size. Null if not stated."
    )
    method: Optional[str] = None
    quantity: int = 1
    text: str = ""
    source: str = "llm"


class MohsSection(BaseModel):
    label: Optional[str] = None
    location: Optional[str] = None
    stages: Optional[int] = Field(None, description="Number of Mohs stages performed. Null if not stated.")
    text: str = ""
    source: str = "llm"


class ClosureSection(BaseModel):
    type: Optional[str] = Field(
        None,
        description="'complex', 'intermediate', or 'adjacent'. Infer from words like 'layered'→intermediate, 'complex repair'→complex, 'adjacent tissue transfer'→adjacent."
    )
    size: Optional[float] = Field(None, description="Final closure size in cm. Null if not stated.")
    location: Optional[str] = None
    location_group: Optional[str] = Field(
        None,
        description="'trunk', 'extremities' (scalp/arm/leg), 'high_risk' (face/hand/foot/neck), or 'critical' (nose/lip/ear/eyelid)."
    )
    group_key: Optional[str] = Field(
        None,
        description="Computed as '{type}_{location_group}'. Leave null — system computes it."
    )
    text: str = ""
    source: str = "llm"


class SrtSection(BaseModel):
    kv: Optional[float] = Field(None, description="Energy in kilovolts. Null if not documented.")
    delivery_type: Optional[str] = Field(
        None,
        description="'superficial' if kV <= 150, 'orthovoltage' if kV > 150, null if kV unknown."
    )
    ultrasound: bool = Field(False, description="True only if ultrasound imaging is explicitly mentioned.")
    images_present: bool = Field(False, description="True only if actual image evidence is documented in the note.")
    text: str = ""
    source: str = "llm"


class DebridementSection(BaseModel):
    depth: Optional[str] = Field(
        None,
        description="'partial' (partial thickness/superficial/shave), 'full' (full thickness), or 'subcutaneous'. Null if depth not stated."
    )
    nail: bool = Field(False, description="True if nail debridement is explicitly mentioned.")
    dermatologic: bool = Field(False, description="True if eczematous, infected, crusted, or dermatologic skin — not a wound/ulcer.")
    is_wound: bool = Field(False, description="True if wound or ulcer debridement.")
    quantity: int = 1
    location: Optional[str] = None
    method: Optional[str] = None
    text: str = ""
    source: str = "llm"


class XtracSection(BaseModel):
    location: Optional[str] = None
    quantity: int = 1
    total_area: Optional[float] = Field(
        None,
        description="Total body surface area treated in sq cm. Null if not documented."
    )
    text: str = ""
    source: str = "llm"


class IplSection(BaseModel):
    location: Optional[str] = None
    method: Optional[str] = Field(
        None,
        description="e.g. 'rosacea', 'skin rejuvenation'. Null if not stated."
    )
    quantity: int = 1
    treatment_area: Optional[float] = Field(None, description="Treatment area in sq cm. Null if not stated.")
    text: str = ""
    source: str = "llm"


class LaserTreatmentSection(BaseModel):
    location: Optional[str] = None
    method: Optional[str] = Field(
        None,
        description="e.g. 'tattoo removal', 'rosacea', 'spider veins'. Null if not stated."
    )
    quantity: int = 1
    text: str = ""
    source: str = "llm"


class FillerSection(BaseModel):
    location: Optional[str] = None
    method: Optional[str] = Field(None, description="Filler brand or type if documented.")
    quantity: int = 1
    text: str = ""
    source: str = "llm"


class FillerMaterialSection(BaseModel):
    location: Optional[str] = None
    quantity: int = 1
    used_quantity: int = Field(1, description="Volume used in cc/ml. 1cc = 1mm for billing.")
    text: str = ""
    source: str = "llm"


class ChemicalPeelSection(BaseModel):
    type: Optional[str] = Field(
        None,
        description="'chemical_peel', 'chemical_peel_epidermal', or 'chemical_peel_dermal'."
    )
    location: Optional[str] = None
    method: Optional[str] = None
    chemical: Optional[str] = Field(None, description="Chemical agent used if named.")
    choice: Optional[str] = Field(None, description="'epidermal' or 'dermal'. Null if not stated.")
    quantity: int = 1
    area_treated: Optional[str] = None
    text: str = ""
    source: str = "llm"


# ─────────────────────────────────────────────────────────────
# UNRESOLVED PROCEDURE
# ─────────────────────────────────────────────────────────────

class UnresolvedProcedure(BaseModel):
    description: str = Field(
        ...,
        description="What procedure was mentioned and why it could not be fully parameterized."
    )
    reason: str = Field(
        ...,
        description="'missing_size', 'missing_location', 'missing_quantity', 'ambiguous_type', 'boundary_case', or 'unknown'."
    )


# ─────────────────────────────────────────────────────────────
# TOP-LEVEL EXTRACTION OUTPUT
# ─────────────────────────────────────────────────────────────

class ProcedureExtractionOutput(BaseModel):
    excision_sections: List[ExcisionSection] = []
    biopsy_sections: List[BiopsySection] = []
    destruction_sections: List[DestructionSection] = []
    shave_removal_sections: List[ShaveRemovalSection] = []
    mohs_sections: List[MohsSection] = []
    closure_sections: List[ClosureSection] = []
    srt_sections: List[SrtSection] = []
    debridement_sections: List[DebridementSection] = []
    xtrac_sections: List[XtracSection] = []
    ipl_sections: List[IplSection] = []
    laser_treatment_sections: List[LaserTreatmentSection] = []
    filler_sections: List[FillerSection] = []
    filler_material_sections: List[FillerMaterialSection] = []
    chemical_peel_sections: List[ChemicalPeelSection] = []
    unresolved_procedures: List[UnresolvedProcedure] = []
