# llm_layer/cot_prompts.py
"""
Three focused prompt builders for the Chain-of-Thought coding pipeline.

Step 1 — Clinical Reader   : free-text reasoning, no codes, no JSON
Step 2 — Billing Params    : structured extraction of procedure parameters
Step 3 — Focused Coder     : code assignment from pre-reasoned parameters
"""

import json
from typing import Any, Dict, List, Optional

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from llm_layer.note_extraction_schema import ProcedureExtractionOutput
from llm_layer.coding_prompt import OutputSchema, _slim_confirmed, _slim_candidate


# ═════════════════════════════════════════════════════════════════════
# STEP 1 — CLINICAL READER
# Returns plain text. No parser. No codes.
# ═════════════════════════════════════════════════════════════════════

_READER_TEMPLATE = """You are an experienced dermatology medical billing specialist with 15 years of practice.

Read the clinical note below carefully. Think step by step before writing anything.

Your job here is ONLY to understand what happened — do NOT assign any CPT codes yet.

Work through these questions in order:

1. PATIENT CONTEXT
   - Is this a new patient, established patient, or consult visit?
   - What evidence in the note supports this? Quote it.
   - Was visit time documented? If yes, how many minutes exactly?

2. PROCEDURES PERFORMED
   For EACH procedure you identify, state:
   - What type of procedure (excision, biopsy, destruction, shave removal, Mohs, closure, SRT, IPL, laser, filler, debridement, chemical peel, XTRAC)?
   - Exact anatomical location as written in the note
   - Any measurements (size, area, energy) — quote the exact text
   - Quantity of lesions or sites
   - Method or technique if documented
   - Whether it is benign, premalignant, or malignant — quote the diagnostic term used

3. DIAGNOSES
   - List every ICD-10 code or diagnosis mentioned
   - Which diagnosis links to which procedure?

4. EVALUATION AND MANAGEMENT
   - Was there a separately documented evaluation beyond the procedures?
   - What clinical reasoning or management decisions are documented?

5. AMBIGUITIES AND GAPS
   - What required billing parameters are missing or unclear?
   - Are any sizes at or near a typical code boundary (e.g. exactly 1.0cm, 2.0cm)?
   - What would a coder need to ask the physician to clarify?

6. UNRESOLVED PROCEDURES
   - Are there any procedures mentioned that you cannot fully parameterize due to missing information?

Be specific. Quote the note where relevant. This analysis will drive the code assignment in the next step.

────────────────────────────────────
CLINICAL NOTE:
{note_data}
────────────────────────────────────

Think step by step:"""

_READER_PROMPT = ChatPromptTemplate.from_template(_READER_TEMPLATE)


def build_clinical_reader_prompt(note: Dict[str, Any]) -> str:
    note_text = json.dumps(note, indent=2, default=str)
    return _READER_PROMPT.format(note_data=note_text)


# ═════════════════════════════════════════════════════════════════════
# STEP 2 — BILLING PARAMETERIZER
# Returns structured JSON matching ProcedureExtractionOutput schema.
# ═════════════════════════════════════════════════════════════════════

_PARAMS_TEMPLATE = """You are a dermatology medical billing specialist extracting structured procedure parameters.

You have already analysed this note:

CLINICAL ANALYSIS:
{clinical_summary}

────────────────────────────────────
ORIGINAL NOTE (for verification):
{note_data}
────────────────────────────────────

Now extract structured billing parameters for every procedure identified.

## EXTRACTION RULES — READ CAREFULLY

1. ONLY extract values EXPLICITLY documented in the note.
2. Set any field to null if the value is not directly stated — NEVER estimate or calculate.
3. For sizes: always use the MAXIMUM dimension from any X×Y measurements.
4. Size priority for excision: Excision Size → Wound Size → Closure Size. NEVER lesion size.
5. For biopsy text field: include the method word ("punch", "tangential", "shave", "incisional") if known.
6. For closure type: "complex" = complex repair / full-thickness, "intermediate" = layered/intermediate, "adjacent" = adjacent tissue transfer.
7. For destruction type: "db" = benign lesions, "dpm" = actinic keratosis/premalignant, "dm" = malignant lesions.
8. For shave removal location_group: "face" = face/ears/eyelids/nose/lips/mucous membrane, "trunk" = trunk/arms/legs, "special" = scalp/neck/hands/feet/genitalia.
9. If a procedure is mentioned but required billing fields (size, quantity, location) are absent → add to unresolved_procedures with reason.
10. Flag BOUNDARY CASES (size within ±0.3cm of a code boundary) in unresolved_procedures with reason "boundary_case" — still include in the relevant section too.

## KNOWN CPT SIZE BOUNDARIES (flag if within ±0.3cm)
- Excision benign: 0.5, 1.0, 2.0, 3.0, 4.0 cm
- Excision malignant: 0.5, 1.0, 1.5, 2.0, 3.0, 4.0 cm
- Shave removal: 0.5, 1.0, 2.0 cm
- Closure (complex): 2.5, 7.5, 20.0 cm
- Closure (intermediate): 2.5, 7.5, 12.5, 20.0 cm

## OUTPUT FORMAT
{format_instructions}"""

_PARAMS_PROMPT = ChatPromptTemplate.from_template(_PARAMS_TEMPLATE)


_COMPACT_OUTPUT_TEMPLATE = """\
Return ONLY valid JSON. No markdown. No extra text. Use null for any field not documented.

{
  "excision_sections":        [{"label":null,"size":null,"location":null,"lesion_type":"benign|malignant|null","quantity":1,"text":"","source":"llm"}],
  "biopsy_sections":          [{"label":null,"location":null,"quantity":1,"text":"include method word: punch/tangential/shave/incisional","source":"llm"}],
  "destruction_sections":     [{"label":null,"destruction_type":"db|dpm|dm|null","location":null,"quantity":null,"method":null,"size":null,"text":"","source":"llm"}],
  "shave_removal_sections":   [{"label":null,"location":null,"location_group":"face|trunk|special|null","size":null,"method":null,"quantity":1,"text":"","source":"llm"}],
  "mohs_sections":            [{"label":null,"location":null,"stages":null,"text":"","source":"llm"}],
  "closure_sections":         [{"type":"complex|intermediate|adjacent|null","size":null,"location":null,"location_group":"trunk|extremities|high_risk|critical|null","group_key":null,"text":"","source":"llm"}],
  "srt_sections":             [{"kv":null,"delivery_type":"superficial|orthovoltage|null","ultrasound":false,"images_present":false,"text":"","source":"llm"}],
  "debridement_sections":     [{"depth":"partial|full|subcutaneous|null","nail":false,"dermatologic":false,"is_wound":false,"quantity":1,"location":null,"method":null,"text":"","source":"llm"}],
  "xtrac_sections":           [{"location":null,"quantity":1,"total_area":null,"text":"","source":"llm"}],
  "ipl_sections":             [{"location":null,"method":null,"quantity":1,"treatment_area":null,"text":"","source":"llm"}],
  "laser_treatment_sections": [{"location":null,"method":null,"quantity":1,"text":"","source":"llm"}],
  "filler_sections":          [{"location":null,"method":null,"quantity":1,"text":"","source":"llm"}],
  "filler_material_sections": [{"location":null,"quantity":1,"used_quantity":1,"text":"","source":"llm"}],
  "chemical_peel_sections":   [{"type":"chemical_peel|chemical_peel_epidermal|chemical_peel_dermal|null","location":null,"method":null,"chemical":null,"choice":"epidermal|dermal|null","quantity":1,"area_treated":null,"text":"","source":"llm"}],
  "unresolved_procedures":    [{"description":"what was found and why it cannot be fully parameterized","reason":"missing_size|missing_location|missing_quantity|ambiguous_type|boundary_case|unknown"}]
}

Only include sections that have procedures to report. Empty sections may be omitted.\
"""


def _slim_format_instructions() -> str:
    return _COMPACT_OUTPUT_TEMPLATE


def build_billing_params_prompt(
    clinical_summary: str,
    note: Dict[str, Any],
) -> tuple:
    parser = JsonOutputParser(pydantic_object=ProcedureExtractionOutput)

    formatted = _PARAMS_PROMPT.format(
        clinical_summary=clinical_summary or "No prior analysis available — extract directly from the note.",
        note_data=json.dumps(note, indent=2, default=str),
        format_instructions=_slim_format_instructions(),
    )
    return parser, formatted


# ═════════════════════════════════════════════════════════════════════
# STEP 3 — FOCUSED CODER
# Replaces the one-shot build_coding_prompt.
# Parameters are pre-structured; prompt focuses on assignment only.
# ═════════════════════════════════════════════════════════════════════

_CODER_TEMPLATE = """You are a certified dermatology medical coder. The note has already been analysed and procedure parameters have been extracted. Your job is to assign the correct CPT codes.

## YOUR TASKS
1. PRE-SELECTED CODES: include ALL of them in cpt_codes unchanged — only add linked_dx.
2. AMBIGUOUS CANDIDATES: for any procedure not covered by a pre-selected code, select the best matching CPT from the supplementary lookup.
3. Do NOT assign an E/M code — leave em_code.code as "".
4. Do NOT assign modifiers — the system handles them after your output.
5. Never output quantity = 0. Omit any code with quantity 0.
6. Never hallucinate codes outside the supplementary lookup.

## GROUPING RULE
- Same CPT + Same DX + Same location → ONE entry, quantity = total count
- Same CPT + Different DX → SEPARATE entries
- Same CPT + Same DX + Different location → SEPARATE entries

## CODE ASSIGNMENT RULES

EXCISION:
- Match size range and location from the pre-extracted parameters
- Size boundary at the upper limit of a range → assign the code WHOSE maxSize includes that value

BIOPSY:
- Total CPT quantity MUST equal total biopsy site count
- Use add-on code alongside primary for additional biopsies

DESTRUCTION:
- Never mix DB (benign), DPM (premalignant), DM (malignant) codes
- DM: match size range from description; list separately per location

CLOSURE:
- Use closure_aggregated total_size (already summed)
- ONE primary code per group + add-on only if total_size > primary maxSize

MOHS:
- Each site = separate CPT entry — NEVER merge sites even if code and stages are the same
- first_stage=1, additional_stages = stages - 1 (add-on code only if stages > 1)

SHAVE REMOVAL:
- Match anatomical group (face/trunk/special) and size range

ICD-10 / DX:
- Link all relevant diagnosis codes to each CPT

────────────────────────────────────
PRE-EXTRACTED PROCEDURE PARAMETERS:
{procedure_params}

────────────────────────────────────
PRE-SELECTED CODES (selector-confirmed — include unchanged, add linked_dx only):
{pre_selected_codes}

────────────────────────────────────
SUPPLEMENTARY CODE LOOKUP (for procedures not covered above):
{supplementary_codes}

────────────────────────────────────
DIAGNOSES AVAILABLE:
{diagnoses}
{web_refs_section}
────────────────────────────────────
{format_instructions}"""

_CODER_PROMPT = ChatPromptTemplate.from_template(_CODER_TEMPLATE)


def build_focused_coder_prompt(
    note: Dict[str, Any],
    parsed: Dict[str, Any],
    confirmed_codes: List[Dict],
    ambiguous_candidates: List[Dict],
    web_refs: Optional[List[str]] = None,
) -> tuple:
    parser = JsonOutputParser(pydantic_object=OutputSchema)
    format_instructions = parser.get_format_instructions()

    web_refs_section = ""
    if web_refs:
        refs_text = "\n\n".join(f"[Reference {i+1}]\n{r}" for i, r in enumerate(web_refs))
        web_refs_section = f"\n────────────────────────────────────\nREFERENCE MATERIAL (from current billing guidelines):\n{refs_text}\n"

    slim_confirmed = [_slim_confirmed(c) for c in confirmed_codes]
    slim_ambiguous = [_slim_candidate(c) for c in ambiguous_candidates]

    # Slim the parsed dict to what's billing-relevant (exclude raw text blocks)
    billing_params = _slim_parsed_for_prompt(parsed)

    formatted = _CODER_PROMPT.format(
        procedure_params=json.dumps(billing_params, indent=2, default=str),
        pre_selected_codes=json.dumps(slim_confirmed, indent=2),
        supplementary_codes=json.dumps(slim_ambiguous, indent=2),
        diagnoses=note.get("diagnoses") or "Not documented",
        web_refs_section=web_refs_section,
        format_instructions=format_instructions,
    )
    return parser, formatted


def _slim_parsed_for_prompt(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip heavy text fields from parsed sections before sending to the coder LLM.
    Keep only the billing-relevant parameters.
    """
    KEEP_KEYS = {
        "excision_sections": ["label", "size", "location", "lesion_type", "quantity"],
        "biopsy_sections": ["label", "location", "quantity"],
        "destruction_sections": ["label", "destruction_type", "location", "quantity", "size", "method"],
        "shave_removal_sections": ["label", "location", "location_group", "size", "quantity"],
        "shave_removal_aggregated": ["location_group", "size", "quantity"],
        "mohs_sections": ["label", "location", "stages"],
        "closure_aggregated": ["type", "group_key", "total_size", "locations"],
        "srt_sections": ["kv", "delivery_type", "ultrasound", "images_present"],
        "debridement_sections": ["depth", "nail", "dermatologic", "is_wound", "quantity", "location"],
        "xtrac_sections": ["location", "quantity", "total_area"],
        "ipl_sections": ["location", "method", "quantity", "treatment_area"],
        "laser_treatment_sections": ["location", "method", "quantity"],
        "filler_sections": ["location", "method", "quantity"],
        "filler_material_sections": ["location", "quantity", "used_quantity"],
        "chemical_peel_sections": ["type", "location", "method", "chemical", "choice", "quantity"],
        "chemical_peel_aggregated": ["type", "method", "location", "quantity"],
    }

    slimmed: Dict[str, Any] = {}

    # Include has_ flags
    for k, v in parsed.items():
        if k.startswith("has_"):
            slimmed[k] = v

    # Slim each section list
    for section_key, keep_fields in KEEP_KEYS.items():
        sections = parsed.get(section_key)
        if not sections:
            continue
        slimmed[section_key] = [
            {f: sec.get(f) for f in keep_fields if f in sec}
            for sec in sections
        ]

    # Include em_data signals
    if parsed.get("em_data"):
        slimmed["em_data"] = parsed["em_data"]

    # Include unresolved procedures
    if parsed.get("unresolved_procedures"):
        slimmed["unresolved_procedures"] = parsed["unresolved_procedures"]

    return slimmed
