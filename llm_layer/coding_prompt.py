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

RULES for Special Scenario:
  - If the same CPT code applies to multiple findings:

  → If ALL linked Dx codes are the SAME:
      → Use ONE entry with quantity = total count

  → If linked Dx codes are DIFFERENT:
      → Create SEPARATE entries for each Dx
      → Each entry must have:
         - quantity = count for that Dx only
         - correct linked_dx

- NEVER merge CPT codes across different Dx codes
- CPT grouping MUST be done by (code + Dx), not by code alone
- When multiple lesions/sites/biopsy exist, map EACH site to its own Dx first, then group CPT codes by (code + Dx)
-------------------------
CRITICAL RULES:
- For now just focus on assigning E/M codes, and CPT codes related to biopsyNotes and mohsNotes from the note data and retrieved codes.
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
   → For tumors/mohs on high risk areas: head, neck, face, jaw, scalp, ears, eyelids, nose, lips, hands, feet, genitalia → assign 17311 / assign 17312(for each additional stage only)
   → For tumors/mohs on TRUNK, ARMS, OR LEGS (e.g., chest, back, abdomen, shoulders, thighs) → assign 17313 / assign 17314(for each additional stage only)

4. Compute totals (MANDATORY):
   → total_first_stage = number of sites
   → total_additional_stage = sum(additional for all sites)

5. FINAL ENFORCEMENT (CRITICAL):
   → quantity(17311/17313) = total_first_stage
   → quantity(17312/17314) = total_additional_stage

6. OUTPUT RULE (CRITICAL):
   → Include additional-stage CPT codes (17312/17314) ONLY if total_additional_stage > 0
   → If total_additional_stage = 0 → DO NOT include 17312/17314 at all

- NEVER output CPT codes with quantity = 0
- If quantity = 0 → remove that CPT code from final output
-------------------------
EXCISION LOGIC (STRICT):

1. Identify each excision section

2. SIZE PRIORITY:
   → Excision Size → Wound Size → Final Closure Size  
   ❌ NEVER use Lesion Size

3. Diameter:
   → If (X x Y) → use MAX(X,Y)

4. CODE:
   → Match size range + anatomical location

5. MULTIPLE LESIONS:
   - If multiple lesions in SAME section/location:
     → DO NOT repeat CPT
     → quantity = lesion count

6. GROUPING:
   - Group by (CPT + Dx)
   - Same CPT + same Dx → ONE entry with summed quantity

7. RESTRICTIONS:
   ❌ No closure codes  
   ❌ No size inference  
   ❌ No quantity = 0  

8. VALIDATION:
   - Size must fall in CPT range
   - Location must match CPT description
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