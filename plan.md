# LEGEND AI – PRODUCTION GRADE DERMATOLOGY CODING SYSTEM (95-98% TARGET)

## Objective

Build a dermatology coding platform capable of:

* 95–98% coding accuracy
* 95%+ CPT coverage for dermatology encounters
* Deterministic billing whenever possible
* Site-aware coding
* Modifier compliance
* Reconstruction compliance
* Audit-ready output

The LLM is an ambiguity resolver and auditor.

The LLM is NOT the primary coder.

---

# ARCHITECTURE

Pipeline:

Fetch
→ Clean
→ Clinical Parse
→ Site Builder
→ Deterministic Selectors
→ Candidate Retrieval
→ LLM Ambiguity Resolver
→ Validation Layer
→ E/M Validation
→ Modifier Engine
→ Confidence Engine
→ Reasoning Engine
→ Output

---

# PHASE 1 — SITE-CENTRIC DATA MODEL

Create:

```python
class ProcedureSite:
    location
    location_group
    diagnosis_codes
    lesion_count
    procedures
```

Every procedure must belong to a site.

No CPT can exist without site ownership.

---

# PHASE 2 — CPT KNOWLEDGE SERVICE

Create:

services/knowledge_base.py

Load:

* proCodeList.csv
* modifierList.csv
* enmCodeList.csv

Provide:

```python
get_cpt(code)
get_size_range(code)
is_addon(code)
parent_code(code)
requires_laterality(code)
can_bill_with_em(code)
```

Selectors must use this service.

No selector should load CSVs directly.

---

# PHASE 3 — EXPAND PROCEDURE COVERAGE

Current coverage is not enough.

Implement deterministic selectors for ALL major dermatology procedure families.

---

## SURGICAL DERMATOLOGY

### Biopsy

11102–11107

### Shave Removal

11300–11313

### Benign Excision

11400–11471

### Malignant Excision

11600–11646

### Mohs

17311–17315

### Simple Closure

12001–12021

### Intermediate Closure

12031–12057

### Complex Closure

13100–13153

### Adjacent Tissue Transfer

14000–14350

### Skin Substitute / Grafts

15040–15278

---

## DESTRUCTION PROCEDURES

### Premalignant

17000
17003
17004

### Benign

17110
17111

### Malignant

17260–17286

---

## SKIN TAG REMOVAL

11200
11201

Must support:

* quantity logic
* add-on logic

---

## CURETTAGE / ED&C

17260–17286

Common in dermatology.

Implement dedicated parser.

---

## INCISION & DRAINAGE

10060
10061

Support:

* cysts
* abscesses

---

## NAIL PROCEDURES

11730
11732
11750
11765

If present in practice.

---

## INTRALESIONAL INJECTIONS

11900
11901

Examples:

* Keloids
* Alopecia

---

## PHOTODYNAMIC THERAPY

96567
96573
96574

---

## LASER PROCEDURES

Support if clinic performs them.

---

## SRT

Radiation treatment selector.

---

## XTRAC

Dedicated selector.

---

## COSMETIC DERMATOLOGY

### Chemical Peel

### IPL

### Fillers

### Neurotoxins (Botox)

If documented.

---

# PHASE 4 — SITE-LEVEL VALIDATION

Create:

site_validator.py

Rules:

Every CPT must belong to:

* lesion
* diagnosis
* site

Reject orphan procedures.

---

# PHASE 5 — LESION CONFLICT VALIDATION

Create:

lesion_validator.py

Reject:

11102 + 11310

same lesion

---

Reject:

11102 + 114xx

same lesion

---

Reject:

11102 + 116xx

same lesion

---

Reject:

113xx + 114xx

same lesion

---

Allow only when:

different site

or

explicitly documented separate lesion.

---

# PHASE 6 — RECONSTRUCTION VALIDATION

Create:

reconstruction_validator.py

---

ATT includes closure.

Reject:

14040 + 13101

same defect

---

Reject:

14040 + 12032

same defect

---

Reject:

14040 + 13121

same defect

---

Allow:

different sites.

---

# PHASE 7 — E/M VALIDATION

Create:

em_validator.py

Do NOT bill E/M automatically.

Require evidence.

Examples:

* assessment
* management discussion
* treatment planning
* diagnostic workup

Reject procedure-only encounters.

---

# PHASE 8 — MODIFIER ENGINE REWRITE

Remove:

"multiple procedures = 59"

completely.

---

Modifier 59:

Require:

* different lesion
* different site
* different incision

Otherwise reject.

---

Modifier 25:

Require separate E/M.

---

Add-on codes:

Never receive modifiers.

---

LT/RT:

Require explicit documentation.

---

# PHASE 9 — DERMATOLOGY RULE ENGINE

Create:

dermatology_rules.py

Implement:

50–100 high-value specialty rules.

Examples:

11102 + 11310

same lesion

→ reject

---

14040 + closure

same defect

→ reject

---

Mohs + excision

same lesion

→ reject

---

# PHASE 10 — DIAGNOSIS VALIDATION

Every CPT must have linked diagnosis.

Reject:

empty diagnosis list.

Ensure linked diagnosis exists within note diagnosis field.

---

# PHASE 11 — ADJACENT TISSUE TRANSFER SELECTOR

Create deterministic selector.

Remove LLM dependency.

Inputs:

* defect size
* location

Return:

14000
14020
14040
14060
etc.

directly.

---

# PHASE 12 — CONFIDENCE ENGINE

Score:

Selector CPT

* validation
* site ownership
* dx ownership

Lower score for:

* LLM-selected CPT
* unresolved procedures
* audit flags

Routes:

AUTO_APPROVE

REVIEW

MANUAL CODER

---

# PHASE 13 — COVERAGE REPORTING

Create:

coverage_report.py

Track:

* CPT frequency
* unsupported procedures
* LLM-selected procedures
* manual review rate

This identifies missing selectors.

---

# PHASE 14 — AUDIT STORAGE

Store:

* parsed data
* site ownership
* CPT decisions
* modifier decisions
* validation results
* reasoning output

Enable post-billing audits.

---

# SUCCESS CRITERIA

Support:

* Mohs
* Excision
* Closure
* ATT
* Biopsy
* Shave Removal
* Destruction
* Cryotherapy
* Skin Tags
* ED&C
* Nail Procedures
* Injections
* PDT
* XTRAC
* SRT
* IPL
* Peels
* Fillers
* Botox

with deterministic selection whenever documentation is sufficient.

Expected outcome:

95–98% dermatology coding coverage with human review only for rare, undocumented, or ambiguous encounters.
