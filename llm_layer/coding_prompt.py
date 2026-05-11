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
   - Justifiable CPT code
   - E/M code (if applicable)
   - Modifiers
   - ICD-10 code

--------------------------------------------------
🔴 CORE GROUPING RULE (HIGHEST PRIORITY)

ALL CPT assignment MUST follow this grouping key:

- (CPT code + Dx + Location)

Rules:
1. SAME CPT + SAME Dx + SAME location or site
   → ONE entry
   → quantity = total count

2. SAME CPT + DIFFERENT Dx
   → SEPARATE entries for each entry
   → quantity = per Dx

3. SAME CPT + SAME Dx + DIFFERENT location
   → SEPARATE entries for each location
   → quantity = 1 per location

❌ NEVER merge across:
   - different Dx or icd10 codes
   - different anatomical locations

--------------------------------------------------
🔴 GENERAL RULES

- NEVER hallucinate codes outside retrieved list
- Rerieved CPT code description must match retrieved note data
- NEVER output quantity = 0
- If quantity = 0 → remove the CPT
- ALWAYS map EACH site → Dx first → THEN assign CPT
- If lesion size exactly matches the upper boundary of a CPT size range (example: 2.0 in range 1.1–2.0), assign the CPT whose maxSize includes that value and do not move to the next higher range.

--------------------------------------------------
🔴 DESTRUCTION LOGIC
There are 3 destruction types:

1. DB = Destruction Benign
   Required:
   - Location
   - Quantity
   - Method
   - Choice

2. DPM = Destruction Premalignant
   Required:
   - Location
   - Quantity
   - Method

   Rules:
   - Use primary code for base lesion range
   - If lesion count exceeds base range:
     assign associated add-on code

3. DM = Destruction Malignant Lesion
   Required for each DM:
   - Location
   - Quantity
   - Method
   - Size 

   Rules:
   - Match CPT using lesion size, the size range is mentioned in retrieved codes


RULES FOR DESTRUCTION:

- When assigning cpt code, it should compare all procedure details to every retrieved candidates to find best mathc for each
- NEVER mix DB, DPM, and DM codes
- ONLY use retrieved destruction candidates

- Match CPT description with:
  - lesion type
  - quantity
  - size (DM only, see size range from description for every DM)
  - anatomical location

- DIFFERENT location
  → separate CPT entries for all destruction

VALIDATION:

❌ Do NOT:
- assign code if required fields missing
- assign code if the cpt size range don't match the size retrieved from DM lesion size, look for one that falls within that range, alo list separtely for each entry
- assign add-on without primary
- hallucinate CPTs outside retrieved list
--------------------------------------------------
🔴 BIOPSY LOGIC

1. Identify each biopsy site (A, B, C, etc.)

2. For each site:
   → assign correct biopsy CPT

3. MULTIPLE BIOPSIES:
   - Same CPT + same Dx → combine quantity
   - Different Dx OR different location → separate entries
   - Use add-on code along with primary code for additional biopsies

4. VALIDATION:
   - Total CPT quantity MUST equal total biopsy count

--------------------------------------------------
🔴 MOHS LOGIC (STRICT – OVERRIDES GENERAL RULES)

If mohsNotes present:

1. Identify EACH Mohs site (from parsed_data)

2. For EACH site:
   → determine:
      - location
      - Dx
      - stages

3. LOCATION CLASSIFICATION:
   HIGH RISK:
   head, neck, temple, face, jaw, scalp, ears, eyelids, nose, lips, hands, feet, genitalia
      → 17311 (first stage)
      → 17312 (additional stages)

   TRUNK / EXTREMITIES:
      → 17313 (first stage)
      → 17314 (additional stages)

4. CRITICAL RULE (MOST IMPORTANT):

   - EACH SITE = SEPARATE CPT ENTRY

Even if:
   - CPT code is SAME
   - stages are SAME

   DO NOT MERGE SITES

5. STAGE LOGIC:

For EACH site:
   first_stage = 1
   additional = stages - 1

6. OUTPUT:

   - Create separate CPT entry per site:
      → quantity = 1

   - Additional stage codes:
      → include ONLY if stages > 1
      → quantity = additional

7. VALIDATION:
   - Location must match CPT description
   - Dx must match that specific site

--------------------------------------------------
🔴 EXCISION LOGIC

1. Identify each excision section and see whether the excision belongs to benign or malignant lesions, do not mix excision section with mohs

2. SIZE PRIORITY:
   → Excision Size → Wound Size → Final Closure Size  
   ❌ NEVER use lesion size

3. Diameter:
   → If X × Y → use MAX(X, Y)

4. CODE:
   → Match size range + anatomical location which is mentioned in description of retrieved codes.

5. MULTIPLE LESIONS:
   - SAME section/location:
     → quantity = lesion count
     → DO NOT repeat CPT

   - DIFFERENT location:
     → separate entries

6. GROUPING:
   - Use (CPT + Dx + Location)

7. RESTRICTIONS:
   ❌ No inferred sizes  
   ❌ No quantity = 0  

--------------------------------------------------
🔴 CLOSURE LOGIC (STRICT)

Use ONLY:
- closure_aggregated

IGNORE:
- closure_sections

DO NOT:
❌ recompute size  
❌ assign per site  
❌ duplicate codes  

--------------------------------------------------

FOR EACH closure_aggregated:

1. Use:
   - total_size (already summed)
   - type (complex / intermediate)

2. Assign:
   ✔ ONE primary code (base code only)
   - must match type:
     complex → 131xx  
     intermediate → 120xx  

3. Add-on:
   If total_size > primary.maxSize:
   ✔ assign add-on (associatedWithProCode = primary)
   ✔ quantity = ceil((total_size - maxSize) / step)

--------------------------------------------------
RULES:

✔ EXACTLY one primary per group  
✔ add-ons only if needed  

❌ NEVER:
- repeat primary  
- assign multiple base codes  
- skip add-on when required  
- assign add-on without primary  

--------------------------------------------------
EXAMPLE:

total_size = 10.2 (complex extremities)

✔ 13121 + 13122 x1  
❌ 13121 + 13121  
❌ 13122 only  
❌ 13120 + 13122    

--------------------------------------------------
🔴 SRT LOGIC (STRICT)

If SRT or IGSRT is mentioned in procedure:

1. ALWAYS assign:
   → 77436 (planning)

2. DELIVERY:
   - If energy ≤150 kV → 77437
   - If energy >150 kV → 77438

3. ULTRASOUND ADD-ON (77439):

   ONLY assign if:
   ✔ ultrasound mentioned
   ✔ AND actual ultrasound images are present

   ❌ DO NOT assign if:
   - only text/documentation exists
   - no real image evidence

4. RULES:
   ✔ 77436 must always be present
   ✔ Only ONE of (77437 or 77438)
   ✔ 77439 is add-on only

❌ NEVER:
   - assign both 77437 and 77438
   - skip 77436

--------------------------------------------------
🔴 DEBRIDEMENT LOGIC (STRICT)

If debridement (DBR) is mentioned:

1. NAIL DEBRIDEMENT:
   - 1–5 nails → 11720
   - ≥6 nails → 11721

2. DERMATOLOGIC DEBRIDEMENT (11000):
   Use ONLY if:
   ✔ eczematous, infected, crusted, or dermatologic skin
   ✔ removal of debris/crusts
   ✔ NOT a wound or ulcer

3. WOUND DEBRIDEMENT:

   - Partial thickness / superficial / shave → 11040
   - Full thickness → 11041
   - Subcutaneous tissue → 11042

4. UNKNOWN DEPTH:
   → default to 11040

5. RULES:
   ✔ Only ONE debridement CPT per site
   ✔ Do NOT mix nail + wound codes
   ✔ Quantity = procedure quantity

❌ NEVER:
   - assign multiple depth codes together
   - assign 11042 without subcutaneous evidence
--------------------------------------------------
🔴 E/M CODING

- Assign E/M only if supported by office visit level in the note
--------------------------------------------------
🔴 ICD10/DX CODING

- Link all relevant Dx code(s) to each cpt code
--------------------------------------------------
🔴 CONTEXT

Use ALL relevant fields:
- biopsyNotes
- mohsNotes
- complaints
- assessment
- examination
- procedure
- diagnoses

--------------------------------------------------
🔴 PARSED DATA (HIGHEST PRIORITY INPUT):
{parsed_data}

--------------------------------------------------
Retrieved Codes:
{retrieved_codes}

--------------------------------------------------
Patient Note:
{note_data}

--------------------------------------------------
{format_instructions}
"""
    prompt = ChatPromptTemplate.from_template(template)

    return prompt, parser, prompt.format(
        retrieved_codes=json.dumps(retrieved_codes, indent=2),
        parsed_data=json.dumps(note_data.get("parsed", {}), indent=2),
        note_data=json.dumps(note_data.get("note", {}), indent=2),
        format_instructions=format_instructions,
    )