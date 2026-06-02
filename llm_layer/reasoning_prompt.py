# llm_layer/reasoning_prompt.py
"""
Reasoning prompt for the accountability layer.

The LLM here is NOT selecting codes — the deterministic engine already did that.
Its job is:
  1. Validate each assigned code against the actual note text (not the selector data)
  2. Extract verbatim supporting evidence from the note for every code
  3. Apply modifier-specific audit rules (-25, -57, -59, LT/RT)
  4. Validate CPT ↔ ICD-10 linkage — each diagnosis must be the documented
     clinical indication for the procedure it is linked to
  5. Flag any decision the note does not clearly support

The note documentation takes precedence over the selector data at every step.
"""

import json
from typing import Any, Dict, List

from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field


# =============================================================
# OUTPUT SCHEMA
# =============================================================

class CodeReasoning(BaseModel):
    code: str

    supporting_evidence: List[str] = Field(
        description=(
            "Verbatim quotes from the patient note that support this code. "
            "Each entry must be a direct quote in double quotes, e.g. "
            "'\"Shave biopsy of right nose performed today.\"' "
            "Do NOT paraphrase. If no verbatim evidence exists, return an empty list "
            "and set confidence_assessment to 'unsupported'."
        )
    )

    justification: str = Field(
        description=(
            "Explain why the supporting_evidence justifies this specific CPT code "
            "and not a different one. Reference the billing rule (size range, "
            "location group, procedure type, quantity) that maps the evidence to "
            "this code."
        )
    )

    modifier_justification: str | None = Field(
        default=None,
        description=(
            "Required if a modifier is present. Apply the specific rule for the modifier:\n"
            "  -25: Confirm a separately identifiable E/M service is documented "
            "beyond the decision to perform the procedure. Quote the note text that "
            "demonstrates the separate service.\n"
            "  -57: Confirm the E/M visit was the initial decision for surgery with "
            "a 90-day global period. Quote the documentation.\n"
            "  -59: Confirm a distinct procedural service is documented — different "
            "lesion, different site, different session, or other qualifying distinction. "
            "Quote the specific text. If no such distinction is documented, mark "
            "confidence_assessment 'unsupported'.\n"
            "  -51: Internal carrier use only — flag immediately as misapplied if present.\n"
            "  LT/RT: Confirm the laterality is explicitly stated in the note."
        )
    )

    dx_justification: str | None = Field(
        default=None,
        description=(
            "For each ICD-10 code linked to this CPT, confirm it is the documented "
            "clinical indication for THIS procedure — not just a diagnosis listed "
            "elsewhere in the note. Quote the note text that links the diagnosis to "
            "the procedure. If a linked diagnosis is not supported as the indication "
            "for this specific procedure, name it and explain why."
        )
    )

    confidence_assessment: str = Field(
        description=(
            "One of: 'supported' | 'partially_supported' | 'unsupported'.\n"
            "  supported: verbatim note evidence clearly supports this exact code.\n"
            "  partially_supported: some evidence exists but is incomplete or ambiguous.\n"
            "  unsupported: the note does not contain evidence for this code."
        )
    )

    flag: str | None = Field(
        default=None,
        description=(
            "Raise a specific concern if the documentation does not clearly support "
            "the code, modifier, or linked diagnosis. Be explicit: name the missing "
            "element and why it matters for billing."
        )
    )


class ReasoningOutput(BaseModel):
    cpt_reasoning: List[CodeReasoning]
    em_reasoning: CodeReasoning | None
    overall_assessment: str = Field(
        description="Brief summary: all codes fully supported, or list what needs human review."
    )
    audit_flags: List[str] = Field(
        default=[],
        description="Concise list of specific concerns that warrant human review before claim submission."
    )


# =============================================================
# PROMPT BUILDER
# =============================================================

def build_reasoning_prompt(
    assigned_codes: List[Dict[str, Any]],
    em_code: Dict[str, Any] | None,
    em_signals: Dict[str, Any],
    modifier_decisions: List[Dict[str, Any]],
    note: Dict[str, Any],
) -> tuple:
    parser = JsonOutputParser(pydantic_object=ReasoningOutput)
    format_instructions = parser.get_format_instructions()

    formatted = (
        "You are a certified professional medical coding auditor (CPC).\n"
        "You are reviewing an AI-generated coding decision for a dermatology claim.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "CORE AUDIT RULES — READ BEFORE PROCEEDING\n"
        "═══════════════════════════════════════════════════════\n\n"

        "0. VALIDATE AGAINST THE PROVIDED CODE DESCRIPTION — NOT FROM MEMORY\n"
        "   Each assigned code includes a 'description' field containing the EXACT CPT\n"
        "   code descriptor text.  Use ONLY this text to determine what sites, sizes, and\n"
        "   procedures the code covers.  Do NOT rely on memorised knowledge of CPT codes —\n"
        "   your training data may be wrong or outdated.\n"
        "   Rule: if the documented site, size, or procedure appears anywhere in the provided\n"
        "   description, that is VALID coverage.  Do NOT flag it as unsupported.\n\n"

        "1. DOCUMENTATION TAKES PRECEDENCE OVER SELECTOR DATA\n"
        "   The selection_data shows what the rule engine used to pick each code.\n"
        "   Do NOT assume the assigned code is correct because the engine assigned it.\n"
        "   If the note documentation conflicts with the selection data, the note wins.\n"
        "   Mark the code 'unsupported' and flag it.\n\n"

        "2. VERBATIM EVIDENCE IS REQUIRED\n"
        "   For every code, populate supporting_evidence with direct quotes from the note.\n"
        "   Paraphrases are NOT acceptable as evidence.\n"
        "   If you cannot find a direct quote that supports a code, set\n"
        "   confidence_assessment = 'unsupported' and supporting_evidence = [].\n\n"

        "   SPECIAL MEASUREMENT RULES:\n"
        "   - For Adjacent Tissue Transfer (ATT), the billing parameter is defect AREA in\n"
        "     square centimetres.  Accept 'final closure size X cm²' OR dimensions 'X × Y cm'\n"
        "     (area = X × Y) as valid size documentation.  Do NOT flag as unsupported simply\n"
        "     because the area is expressed as dimensions rather than a single number.\n"
        "   - For Mohs, count STAGES (each slide read = 1 stage), not blocks or sections.\n"
        "   - For closure, use the FINAL closure length in cm, not the lesion size.\n\n"

        "3. MODIFIER-SPECIFIC AUDIT RULES\n\n"

        "   MODIFIER -25 (E/M same day as procedure):\n"
        "   Verify the note documents a SEPARATELY IDENTIFIABLE evaluation and management\n"
        "   service — history, examination, or medical decision-making — that goes BEYOND\n"
        "   the decision to perform the procedure.\n"
        "   A note that only documents the procedure itself does NOT support -25.\n"
        "   Quote the specific E/M text.\n\n"

        "   MODIFIER -57 (Decision for surgery):\n"
        "   Verify the E/M visit is the initial decision for a surgical procedure\n"
        "   with a 90-day global period. Quote the documentation.\n\n"

        "   MODIFIER -59 (Distinct procedural service):\n"
        "   Verify EXPLICIT documentation of a distinct service:\n"
        "     - Different anatomical site or lesion (must be named separately), OR\n"
        "     - Different session or encounter, OR\n"
        "     - Different procedure type that is not an incidental component.\n"
        "   If the note describes only ONE lesion or ONE site and two codes are billed\n"
        "   with -59, mark -59 as unsupported and flag potential upcoding.\n\n"

        "   MODIFIER LT / RT (Laterality):\n"
        "   Verify the side is explicitly named in the note. Do not infer from context.\n\n"

        "   MODIFIER -51 (Multiple procedures — carrier internal):\n"
        "   This modifier must NEVER appear on a submitted claim.\n"
        "   If present, flag it immediately as misapplied.\n\n"

        "4. CPT ↔ ICD-10 LINKAGE VALIDATION\n"
        "   ICD-10 DOCUMENTATION STANDARD:\n"
        "   ICD-10 codes do NOT need to literally appear in the clinical note.\n"
        "   If the note documents a clinical condition (e.g., 'Squamous Cell Carcinoma',\n"
        "   'malignant melanoma', 'actinic keratosis') that maps to the linked ICD-10 code,\n"
        "   that IS valid documentation.  Do NOT flag it as unsupported simply because the\n"
        "   ICD code string (e.g., 'C44.42') does not appear literally in the note text.\n\n"
        "   Only flag ICD-10 linkage when:\n"
        "     a. The clinical condition itself is NOT mentioned in the note at all, OR\n"
        "     b. The diagnosis IS documented but is clearly NOT the indication for this\n"
        "        specific procedure (e.g., a different lesion or unrelated condition).\n\n"
        "   For each linked diagnosis, confirm the clinical condition appears in the note\n"
        "   and is the documented reason for THIS procedure.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "ASSIGNED CPT CODES WITH SELECTOR DATA:\n"
        "═══════════════════════════════════════════════════════\n"
        f"{json.dumps(assigned_codes, indent=2)}\n\n"

        "═══════════════════════════════════════════════════════\n"
        "E/M CODE:\n"
        "═══════════════════════════════════════════════════════\n"
        f"{json.dumps(em_code, indent=2) if em_code else 'None'}\n\n"
        "E/M SELECTION SIGNALS:\n"
        f"{json.dumps(em_signals, indent=2)}\n\n"

        "═══════════════════════════════════════════════════════\n"
        "MODIFIER DECISIONS:\n"
        "═══════════════════════════════════════════════════════\n"
        f"{json.dumps(modifier_decisions, indent=2)}\n\n"

        "═══════════════════════════════════════════════════════\n"
        "PATIENT NOTE (source of truth — documentation trumps all):\n"
        "═══════════════════════════════════════════════════════\n"
        f"{json.dumps(note, indent=2)}\n\n"

        "═══════════════════════════════════════════════════════\n"
        "AUDIT CHECKLIST — work through this for every code:\n"
        "═══════════════════════════════════════════════════════\n"
        "[ ] Is there verbatim note text supporting this exact code? → supporting_evidence[]\n"
        "[ ] Does the note text match the selection_data, or does it conflict?\n"
        "[ ] If a modifier is present, does the documentation satisfy the modifier-specific rule?\n"
        "[ ] Is each linked ICD-10 the documented clinical indication for THIS procedure?\n"
        "[ ] Is there any reason a payer reviewer would deny or query this code?\n\n"

        f"{format_instructions}\n"
    )

    return None, parser, formatted
