# llm_layer/coding_prompt.py

import json
from typing import Dict, Any

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
    charge_per_unit: bool = False

class EMCode(BaseModel):
    code: str
    modifier: str | None = None
    linked_dx: list[str]
    charge_per_unit: bool = False

class Codes(BaseModel):
    cpt_codes: list[CPTCode]
    em_code: EMCode

class OutputSchema(BaseModel):
    patient_summary: str
    codes: Codes
    # justification removed — reasoning engine owns all per-code justification

# =========================
# PROMPT BUILDER
# =========================

def _slim_confirmed(code: dict) -> dict:
    """Strip internal engine fields before sending confirmed codes to the LLM."""
    keep = {"code", "description", "quantity", "source", "confidence"}
    return {k: v for k, v in code.items() if k in keep}


def _slim_candidate(code: dict) -> dict:
    """Strip internal engine fields before sending ambiguous candidates to the LLM."""
    keep = {"code", "description", "proName", "associatedWithProCode", "type", "minSize", "maxSize"}
    return {k: v for k, v in code.items() if k in keep}


def build_coding_prompt(
    note_data: Dict[str, Any],
    confirmed_codes: list,
    ambiguous_candidates: list,
):

    parser = JsonOutputParser(pydantic_object=OutputSchema)
    format_instructions = parser.get_format_instructions()
    template = """
You are a certified medical coding expert.

Your task:
1. Generate a concise patient summary.
2. PRE-SELECTED CODES: include ALL of them in cpt_codes unchanged — only add linked_dx.
3. SUPPLEMENTARY CODES: for every procedure CLEARLY DOCUMENTED in the note that is NOT
   already covered by a pre-selected code, select the best matching CPT from the
   supplementary lookup and add it to cpt_codes with linked_dx.
4. If there are no pre-selected codes, code ALL documented procedures using the lookup.

Rules:
- Do NOT change pre-selected code values, descriptions, or quantities.
- Only add codes that appear in the supplementary lookup — no hallucinations.
- Do NOT assign modifiers (system handles them after your output).
- Do NOT assign an E/M code — leave em_code.code as empty string "".
- Never output quantity = 0. If quantity is 0, omit that code.
- Do NOT code a procedure that is not explicitly documented in the patient note.
  If a procedure type exists in the supplementary lookup but is not mentioned in
  the note, do not assign it. Omitting a code is always safer than guessing one.

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

QUANTITY VALIDATION:
- DB / DPM: total coded quantity across all entries must equal total lesion count from the note.
- DM: each lesion is coded separately — one entry per lesion, quantity = 1 each.
- If the note does not state a quantity, do not assume one — use parsed_data quantity.
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
🔴 SHAVE REMOVAL LOGIC

1. Identify EACH shave removal section

2. LOCATION GROUPS:

TRUNK:
- trunk
- arms
- legs

FACE:
- face
- ears
- eyelids
- nose
- lips
- mucous membrane

SPECIAL:
- scalp
- neck
- hands
- feet
- genitalia

3. SIZE PRIORITY:

FIRST:
→ Excision Size, including margins

SECOND:
→ Lesion Size

If X × Y:
→ use MAX(X,Y)

4. CPT ASSIGNMENT:

- Match:
  ✔ anatomical group
  ✔ size range

- If NO size exists:
  ✔ assign smallest/base code
  ✔ according to anatomical group only

5. MULTIPLE SHAVE REMOVALS:

If:
- SAME CPT
- SAME Dx
- SAME location group

→ MERGE
→ quantity = total lesions

6. VALIDATION:

❌ NEVER:
- assign wrong anatomical group
- assign higher size bracket incorrectly
- separate identical shave removals unnecessarily

QUANTITY VALIDATION:
- Sum of all shave removal quantities across all entries must equal the
  total number of shave removal lesions documented in the note.
- If size boundary is exactly at a CPT range limit (e.g. 0.5cm on a 0.1–0.5cm code),
  assign the code whose maxSize includes that value — do not move to the next range.

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

QUANTITY VALIDATION:
- quantity per entry = number of lesions at that location with that Dx.
- If size is at the upper boundary of a range (e.g. exactly 2.0cm in a 1.1–2.0cm code),
  assign that code — do not move to the higher range.
- If the note does not document a size, do not assign an excision code at all.

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
🔴 LASER TREATMENT LOGIC

1. Identify EACH laser treatment section

2. REQUIRED:
- Location

3. OPTIONAL:
- Method

4. CPT MATCHING PRIORITY:

STEP 1:
- Match method directly to retrieved CPT description

Examples:
- tattoo → CL002
- rosacea → 96920

STEP 2:
If method missing:
- Search procedure text for keywords
- Match keywords against retrieved CPT descriptions

Examples:
- "spider veins" → CL005
- "birthmark" → CL011

STEP 3:
If NO keyword or method matches but laser treatment IS documented in the note:
→ assign CL001 (general laser treatment)

If laser treatment is NOT clearly documented in the note:
→ do NOT assign any laser code
→ do not use CL001 as a catch-all for unrelated procedures

5. GROUPING:

Same:
- CPT
- Dx
- Location

→ merge quantity

Different location:
→ separate CPT entries

6. VALIDATION:

❌ NEVER:
- hallucinate laser codes
- assign non-retrieved laser CPT
- assign rosacea code unless rosacea mentioned
- assign tattoo removal unless tattoo 

--------------------------------------------------
🔴 FILLER MATERIAL LOGIC

- Assign relevant filler material CPT
- Match the quantity used with the retrieved code to assign the correct one
- 1 cc = 1 mm

--------------------------------------------------
🔴 FILLER LOGIC

- First match method to Retrieved Code Description to assign relevant CPT code
- If method is missing or the mentioned method don't match the descriptionof any retrieved codes, then assign best relevant cpt code to the filler procedures

--------------------------------------------------
🔴 XTRAC LASER LOGIC

1. Xtrac CPT assignment depends ONLY on:
- Total body surface area treated (sq.cm)

2. Use ONLY retrieved Xtrac codes:
- 96920
- 96921
- 96922

3. RANGE LOGIC:

96920:
< 250 sq cm

96921:
250 - 500 sq cm

96922:
> 500 sq cm

4. If:
"Total body surface area treated (sq.cm)"
is missing or empty:

→ assign 96920

5. VALIDATION:

❌ NEVER:
- require location
- hallucinate Xtrac codes
- assign non-retrieved codes
- use diagnosis/location for filtering

--------------------------------------------------
🔴 CHEMICAL PEEL LOGIC

There are 3 chemical peel categories:

1. Chemical Peel
   Codes:
   CP001 - CP009

2. Chemical Peel Epidermal
   Codes:
   15788, 15792

3. Chemical Peel Dermal
   Codes:
   15789, 15793

--------------------------------------------------
RULES:

1. Use ONLY retrieved candidates

2. Chemical Peel:
   - Match method keywords against code description
   - If no method matched:
     → fallback = CP001

3. Epidermal / Dermal:
   - Match choice keywords against code description
   - If no choice match:
     → return all retrieved candidates

4. GROUPING:
   - SAME CPT + SAME location + SAME Dx
     → merge quantity

5. NEVER hallucinate chemical peel codes

--------------------------------------------------
🔴 IPL LOGIC

1. Identify EACH IPL procedure

2. Extract:
   - Method
   - Location
   - Quantity
   - Treatment Area

3. CPT ASSIGNMENT PRIORITY:

PRIORITY 1:
- If Method exists:
  assign CPT whose description matches method

Examples:
- Rosacea
- Skin Rejuvenation

PRIORITY 2:
- If no method:
  assign CPT using treatment area range
  using:
    - minSize
    - maxSize

PRIORITY 3:
- If no area:
  assign default CPT:
    96920

4. VALIDATION

❌ NEVER:
- hallucinate IPL codes
- assign CPT outside retrieved IPL candidates
- ignore method when present

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

E/M code selection is handled DETERMINISTICALLY by the system after your output.
- Set em_code to: {"code": "", "modifier": null, "linked_dx": []}
- Do NOT assign or guess an E/M code yourself
- Do NOT leave a placeholder like "99213" — leave code as empty string ""
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
PATIENT NOTE (source of truth — always takes precedence):
{note_data}

--------------------------------------------------
PARSED DATA (system-extracted procedure parameters — use as a guide, but if
parsed_data conflicts with what the note actually says, the note wins):
{parsed_data}

--------------------------------------------------
PRE-SELECTED PROCEDURE CODES (rule engine output — do not modify):
The following CPT codes were selected deterministically.
Include every one of them in your output cpt_codes list exactly as given.
Your only task for these is to fill in linked_dx.
{pre_selected_codes}

--------------------------------------------------
SUPPLEMENTARY CODE LOOKUP — select from these only for procedures NOT already covered above:
{supplementary_codes}

--------------------------------------------------
{format_instructions}
"""
    slim_confirmed = [_slim_confirmed(c) for c in confirmed_codes]
    slim_ambiguous = [_slim_candidate(c) for c in ambiguous_candidates]

    # Direct string substitution — no ChatPromptTemplate so no template
    # escaping issues. JSON literals in the rules use literal { } characters
    # since we're not inside a format string.
    formatted = (
        template
        .replace("{note_data}",        json.dumps(note_data.get("note", {}), indent=2))
        .replace("{parsed_data}",      json.dumps(note_data.get("parsed", {}), indent=2))
        .replace("{pre_selected_codes}", json.dumps(slim_confirmed, indent=2))
        .replace("{supplementary_codes}", json.dumps(slim_ambiguous, indent=2))
        .replace("{format_instructions}", format_instructions)
    )

    return None, parser, formatted