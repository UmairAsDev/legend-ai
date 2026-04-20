# llm_layer/coding_prompt.py
"""
Contains prompt template for medical coding extraction.
"""

import json
from typing import Dict, Any


def build_coding_prompt(note_data: Dict[str, Any], retrieved_codes) -> str:
    """
    Build structured prompt for LLM.

    Args:
        note_data (dict): Patient note data

    Returns:
        str: formatted prompt
    """

    return f"""
You are a highly experienced certified medical dermatology and cosmetic procedure expert.

Your task is to:
1. Analyze the patient note carefully
2. Display a structured summary of the patient data
3. Assign accurate:
   - CPT codes, if same procedure repeated then generate the code for that and then add the number in "quantity" parameter sequentially like 1, 2 or 3 so on,,,
   - E/M code (if applicable)
   - Modifiers (applicable on both CPT and E/M codes)
   - ICD-10 codes (Dx Codes applicable on both CPT and E/M codes)
4. Correctly LINK diagnosis codes (ICD-10 codes) to procedures and E/M

---

### Retrieved Candidate Codes:
{retrieved_codes}

### Patient Note Data:
{json.dumps(note_data, indent=2)}

---
### Coding Rules:
- Use ONLY valid CPT and E/M codes
- For CPT correlate procedures or biopsy or mohs with the assessment, use the keywords to identify accurate cpt codes
- Also check whther the input data contain any details related to cosmetic related, then in cpt codes, you will generate internal or payer-specific code
- E/M Code rules for (New) and (Established) patients:
| Level | Patient Code (New) | Patient Code (Established) | Time (New) | Time (Established) | Problem Focused History (New) | Problem Focused Examination (New) | Medical Decision (New) | Problem Focused History (Established) | Problem Focused Examination (Established) | Medical Decision (Established) |
| ----- | ------------------ | -------------------------- | ---------- | ------------------ | ----------------------------- | --------------------------------- | ---------------------- | ------------------------------------- | ------------------------------------------ | ------------------------------ |
| 1     | 99201              | 99211                      | 10–19 min  | 05–09 min          | Simple                        | Simple                            | Straightforward        | None                                  | None                                       | Minimal                        |
| 2     | 99202              | 99212                      | 20–29 min  | 10–14 min          | Expanded                      | Expanded                          | Straightforward        | Simple                                | Simple                                     | Straightforward                |
| 3     | 99203              | 99213                      | 30–44 min  | 15–24 min          | Detailed                      | Detailed                          | Low Complexity         | Expanded                              | Expanded                                   | Low Complexity                 |
| 4     | 99204              | 99214                      | 45–59 min  | 25–39 min          | Comprehensive                 | Comprehensiv                      | Moderate Complexity    | Detailed                              | Detailed                                   | Moderate Complexity            |
| 5     | 99205              | 99215                      | 60–74 min  | 40–54 min          | Comprehensive                 | Comprehensive                     | High Complexity        | Comprehensive                         | Comprehensive                              | High Complexity                |

When given a patient visit description, you must:

Identify whether the patient is New or Established
Determine the visit level (1–5) based on complexity or time, as shown above
Return the correct CPT code for both categories:
    New Patient CPT Code
    Established Patient CPT Code
If time is provided, use it to determine the level
If complexity of issues/problems is provided instead of time, map accordingly
As ICD10 codes or Dx Codes also provided in in the diagnoses, you can first search yourself the relevant cpt and E/M codes related to them and then map them accordingly

- Must apply applicable modifiers to both CPT and E/M codes
- Make sure to not miss any applicable modifiers and ICD-10 codes to CPT codes and E/M codes 
- ICD-10 codes should be related to the diagnosis in the provided data  
- Your response should be based on including all input data.
---

### Output Format (STRICT JSON):

{{
  "patient_summary": "...",
  "codes": {{
    "cpt_codes": [
      {{
        "code": "...",
        "description": "...",
        "modifier": "...",
        "linked_dx": ["..."]
        "quantity": "..."
      }}
    ],
    "em_code": {{
      "code": "...",
      "modifier": "...",
      "linked_dx": ["..."]
    }}
  }},
  "justification": {{
    "cpt": "...",
    "em": "...",
    "modifier": "..."
  }}
}}

Return response in ONLY JSON. No extra text.
"""