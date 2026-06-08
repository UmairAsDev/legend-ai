# Legend AI Dermatology Billing Rulebook

## 1. System Billing Philosophy

### Rule 1.1

Never assign CPT codes directly from raw note text.

Workflow:

Patient Note
→ Procedure Extraction
→ Procedure Validation
→ CPT Rule Engine
→ Modifier Engine
→ ICD Linkage Engine
→ Audit Engine
→ Final Output

### Rule 1.2

LLM may identify procedures but must not determine CPT codes when deterministic rules exist.

### Rule 1.3

If a procedure is identified but required attributes are missing:

* Use fallback coding rules where allowed.
* Mark confidence as fallback.
* Do not discard the procedure unless coding is impossible.

---

# 2. Anatomical Location Normalization

## Head / High Risk Areas

* Forehead
* Temple
* Scalp
* Nose
* Lip
* Eyelid
* Ear
* Tragus
* Helix
* Antihelix
* Cheek
* Chin
* Jaw

## Neck

* Anterior Neck
* Posterior Neck
* Lateral Neck

## Trunk

* Chest
* Abdomen
* Back
* Flank
* Umbilicus

## Upper Extremity

* Shoulder
* Arm
* Forearm
* Wrist
* Hand
* Finger

## Lower Extremity

* Thigh
* Knee
* Leg
* Foot
* Toe

All selectors must use normalized locations.

---

# 3. E/M Assignment Rules

## Automatic Suppression

Do not assign E/M when:

* Mohs Surgery performed
* SRT performed
* XTRAC Laser performed
* Radiation Therapy performed
* Debridement performed
* Cosmetic Fillers performed

Unless explicit separate E/M documentation exists.

## E/M May Be Allowed

* Biopsy
* Excision
* Cryotherapy
* Destruction
* Benign Lesion Treatment

Only when separately identifiable evaluation is documented.

## Modifier 25

Assign only when:

* E/M is separately identifiable
* Procedure performed same day

Otherwise suppress.

---

# 4. Biopsy Rules

## Tangential Biopsy

Primary:
11102

Additional:
11103

Quantity:
Additional lesions count toward 11103 quantity.

## Punch Biopsy

11104
11105

## Incisional Biopsy

11106
11107

## Ear / Tragus Biopsy

Location:
Tragus

Assign:
69100

Modifiers:
RT or LT

Never assign 11102 for tragus biopsy.

---

# 5. Shave Removal Rules

## CPT Series

11300-11313

## Selection Criteria

Location Group
+
Lesion Size

## Missing Size

Do not discard procedure.

Assign smallest CPT within location group.

Confidence:
Fallback

## Quantity Logic

Multiple shave removals:

Same CPT
→ Aggregate quantity

Example:

6 trunk shave removals

11300 Qty 6

---

# 6. Destruction Benign Rules

## Standard Dermatology

17110
17111

## Never Use

46900
54050
56501

Unless anatomical site explicitly supports:

* Anal
* Penis
* Vulva
* Vaginal
* Perineal

## Quantity

1-14 lesions
→ 17110

15+
→ 17111

---

# 7. Destruction Premalignant Rules

Primary

17000

Additional

17003

Example:

11 lesions

17000 Qty 1

17003 Qty 10

Never use total lesion count for 17003.

Formula:

17003 Qty = Total Lesions - 1

---

# 8. Cryotherapy Rules

Determine lesion type first.

Premalignant
→ 17000/17003

Benign
→ 17110/17111

Cryotherapy itself is not a CPT code.

Cryotherapy is only a treatment method.

---

# 9. Excision Rules

## Benign

11400-11446

## Malignant

11600-11646

Selection:

Location Group
+
Excised Diameter Including Margins

Never use lesion size alone.

Use excision size including margins.

---

# 10. Closure Rules

## Simple

12001-12021

## Intermediate

12031-12057

## Complex

13100-13160

## Adjacent Tissue Transfer

14000-14350

Selection:

Location Group
+
Final Closure Size

Repair codes may be billed separately when supported.

---

# 11. Mohs Surgery Rules

## Location Mapping

High Risk Areas

* Forehead
* Nose
* Lip
* Ear
* Eyelid
* Temple

Primary:
17311

Additional:
17312

---

Trunk / Arms / Legs

Primary:
17313

Additional:
17314

## Stage Counting

Per Site

Site A

Stage 1
→ Primary Code

Stage 2
→ Additional Code Qty 1

Stage 3
→ Additional Code Qty 2

Site B

Restart counting.

## Closure

Always evaluate closure separately.

Possible:

* Layered Closure
* Complex Closure
* Adjacent Tissue Transfer

Mohs does not eliminate closure billing.

---

# 12. Radiation Therapy (SRT)

## Simulation

77436

## Delivery

77437

Requirements:

* Energy
* Treatment Parameters
* Fraction Information

Missing required details:
Human Review

No E/M assignment.

---

# 13. XTRAC Laser Rules

## CPT

96920
96921
96922

Selection based on:

Total Body Surface Area

Required:

Treated Area

Missing Area:

Human Review

No E/M assignment.

---

# 14. Debridement Rules

Only structured DBR sections generate codes.

Ignore free-text mentions.

Required:

* Location
* Procedure Evidence

---

# 15. Modifier Engine

## Modifier 25

Separate E/M

## Modifier 59

Distinct Procedure

## RT

Right Side

## LT

Left Side

## XS

Separate Structure

## XE

Separate Encounter

## XP

Separate Practitioner

## XU

Unusual Non-overlapping Service

---

# 16. ICD Linkage Rules

Every CPT must have at least one ICD.

No diagnosis
→ Audit Flag

Incorrect diagnosis family
→ Audit Flag

Unlinked CPT
→ Human Review

---

# 17. Confidence Scoring

Confirmed

All required attributes present.

Likely

Minor attribute missing.

Fallback

Rule-based fallback applied.

Human Review

Critical billing information missing.

Unsupported

Documentation contradicts coding.

---

# 18. Audit Layer

Generate audit flags for:

* Missing size
* Missing location
* Missing diagnosis
* Missing stage count
* Missing closure size
* Missing radiation parameters
* Missing treated area

Audit flags must never suppress valid CPT assignment unless coding is impossible.
