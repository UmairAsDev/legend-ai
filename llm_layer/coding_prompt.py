# llm_layer/coding_prompt.py

import json
from typing import Dict, Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field


# =========================
# 🔹 OUTPUT SCHEMA
# =========================

class CPTCode(BaseModel):
    code: str
    description: str
    modifier: str | None = None
    linked_dx: list[str]
    quantity: str


class EMCode(BaseModel):
    code: str
    modifier: str | None = None
    linked_dx: list[str]


class Codes(BaseModel):
    cpt_codes: list[CPTCode]
    em_code: EMCode


class OutputSchema(BaseModel):
    patient_summary: str
    codes: Codes
    justification: dict


# =========================
# 🔹 PROMPT BUILDER
# =========================

def build_coding_prompt(note_data: Dict[str, Any], retrieved_codes):

    parser = JsonOutputParser(pydantic_object=OutputSchema)

    format_instructions = parser.get_format_instructions()

    template = """
You are a certified medical coding expert.

Your task is to:
1. Analyze the FULL patient note carefully
2. Generate structured patient summary
3. Assign accurate:
   - CPT codes
   - E/M code (if applicable)
   - Modifiers
   - ICD-10 codes

-------------------------
CRITICAL RULES:
- For now just focus on assigning CPT codes related to biopsyNotes and mohsNotes from the note data and retrieved codes.
- NEVER repeat the same CPT code multiple times
- If a procedure is repeated:
  → Use ONE entry
  → quantity = total count

  Ensure the sum of quantities across all biopsy CPT codes equals the total number of biopsies.
  If there is 1 biopsy (e.g., A) → total CPT quantity must be 1
  If there are 4 biopsies (A, B, C, D) → valid distributions include:
    4 different codes with quantity 1 each, or
    mixed quantities (e.g., one code = 1, another = 3)
  Constraint: Total CPT quantity must always match total biopsy count.

MOHS LOGIC (STRICT):
If mohsNotes are present:

1. Identify each site (A, B, etc.)

2. For EACH site:
   → stages = total stages
   → first_stage = 1
   → additional = (stages − 1)

3. Mohs Location rule:
   → For tumors/mohs on high risk areas: head, neck, face, scalp, ears, eyelids, nose, lips, hands, feet, genitalia → assign 17311 / 17312 as cpt code
   → For tumors/mohs on TRUNK, ARMS, OR LEGS (e.g., chest, back, abdomen, shoulders, thighs) → assign 17313 / 17314 as cpt code

4. Compute totals (MANDATORY):
   → total_first_stage = number of sites
   → total_additional_stage = sum(additional for all sites)

5. FINAL ENFORCEMENT (CRITICAL):
   → quantity(17311/17313) = total_first_stage
   → quantity(17312/17314) = total_additional_stage
   → MUST copy these values exactly (no recomputation)

6. Output for cpt codes:
   → Each CPT code only once

- Ensure each retrieved code’s description is accurately matched against the biopsyNotes and mohsNotes in the note data, and assign the appropriate CPT codes accordingly.
-------------------------
CONTEXT:

- Use ALL fields:
  - biopsyNotes
  - mohsNotes
  - complaints
  - assessment
  - examination
  - procedure

-------------------------
STRICT RULES:

- DO NOT hallucinate codes outside retrieved
- Code + description MUST EXACTLY match retrieved data
-------------------------
Retrieved Codes:
{retrieved_codes}

-------------------------
Patient Note:
{note_data}

-------------------------
{format_instructions}
"""

    prompt = ChatPromptTemplate.from_template(template)

    return prompt, parser, prompt.format(
        retrieved_codes=json.dumps(retrieved_codes, indent=2),
        note_data=json.dumps(note_data, indent=2),
        format_instructions=format_instructions,
    )