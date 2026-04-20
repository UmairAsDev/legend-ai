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
1. Analyze the patient note carefully
2. Display a structured summary of the patient data
3. Assign accurate:
   - CPT codes, if same procedure repeated then generate the code for that and then add the number in "quantity" parameter sequentially like 1, 2 or 3 so on,,,
   - E/M code (if applicable)
   - Modifiers (applicable on both CPT and E/M codes)
   - ICD-10 codes (Dx Codes applicable on both CPT and E/M codes)
4. Correctly LINK diagnosis codes (ICD-10 codes) to procedures and E/M

-------------------------
Retrieved Codes:
{retrieved_codes}

-------------------------
Patient Note:
{note_data}

-------------------------
Rules:
- Use valid CPT/E&M
- Link ICD codes correctly
- Apply modifiers properly

-------------------------
{format_instructions}
"""

    prompt = ChatPromptTemplate.from_template(template)

    return prompt, parser, prompt.format(
        retrieved_codes=json.dumps(retrieved_codes, indent=2),
        note_data=json.dumps(note_data, indent=2),
        format_instructions=format_instructions,
    )