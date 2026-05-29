# Legend AI — Dermatology Billing System: Implementation Plan

## Table of Contents
1. [System Purpose](#1-system-purpose)
2. [Current Architecture — What Is Built](#2-current-architecture--what-is-built)
3. [Remaining Work](#3-remaining-work)
4. [File Reference](#4-file-reference)

---

## 1. System Purpose

An AI-driven CPT and E/M coding engine for a dermatology practice. Reads clinical progress notes from an EHR (MySQL), determines every billable procedure and evaluation/management event, assigns the correct CPT codes with modifiers and ICD-10 diagnosis links, and produces a structured superbill output — replacing manual billing staff for the coding step.

---

## 2. Current Architecture — What Is Built

### 2.1 Pipeline

```
fetch → clean → parse → clinical_read → billing_params → web_lookup
      → retrieve → llm → validate → em_modifiers → reasoning
```

**Entry point:** `app/api/route.py` → `GET /api/v1/process-note/{note_id}`  
**Graph:** `app/graph/langgraph_builder.py`  
**Nodes:** `app/services/medical_engine.py`

---

### 2.2 Node Descriptions

| Node | Purpose | File |
|---|---|---|
| `fetch` | MySQL query — joins progressNotes, diagnoses, providers | `src/data_layer/progressnote.py` |
| `clean` | Strip to allowlisted fields, serialize types | `utils/engine_utils.py` |
| `parse` | Regex extraction of 15 procedure types + E/M signals. Runs aggregation passes. | `services/clinical_parser.py` |
| `clinical_read` | **CoT Step 1** — LLM reads note holistically; free-text reasoning; no codes | `app/services/medical_engine.py` |
| `billing_params` | **CoT Step 2** — LLM extracts structured params; merges with regex output (regex wins) | `app/services/medical_engine.py` |
| `web_lookup` | Conditional web search for SRT, IPL, boundary cases, unknown procedures | `services/web_lookup.py` |
| `retrieve` | Deterministic selectors → SQL filter fallback → empty (no semantic search) | `app/services/medical_engine.py` |
| `llm` | **CoT Step 3** — Focused coder; assigns codes from pre-structured params + candidates | `app/services/medical_engine.py` |
| `validate` | **Billing integrity rules** — rejects bundled pairs, orphan add-ons; flags -59 and missing DX | `services/validation_engine.py` |
| `em_modifiers` | Deterministic E/M selection; assigns -25/-57; assigns LT/RT and -59 modifiers | `utils/engine_utils.py` |
| `reasoning` | LLM audits every code against note; produces supporting_evidence[], audit_flags | `services/reasoning_engine.py` |

---

### 2.3 LLM Calls Per Note

| Call | Prompt Size | Purpose |
|---|---|---|
| CoT Step 1 (clinical_read) | ~640 tokens | Understand the note — free-text reasoning |
| CoT Step 2 (billing_params) | ~1,100 tokens | Extract structured procedure parameters |
| Web search | ~0–2 calls | Reference lookup for edge cases (Tavily/DuckDuckGo) |
| CoT Step 3 (llm / focused coder) | ~1,400 tokens | Assign codes from parameters + candidates |
| Reasoning | ~2,400 tokens | Per-code audit: evidence, modifiers, DX linkage |
| **Total** | **~5,500 tokens** | All calls combined |

Fallback (one-shot coding prompt if CoT Step 3 fails): ~4,700 tokens.

---

### 2.4 Deterministic Selectors

Nine procedure types have rule-based selectors — zero LLM involvement:

| Selector | Key Inputs | Code Range |
|---|---|---|
| `BiopsySelector` | method, count | 11100–11107 |
| `ClosureSelector` | total_size, type, location_group | 12001–13160 |
| `DebridementSelector` | nail, dermatologic, is_wound, depth, qty | 11000–11721 |
| `DestructionSelector` | type (db/dpm/dm), qty, size, location | 17000–17286 |
| `ExcisionSelector` | size, location, lesion_type | 11400–11646 |
| `MohsSelector` | location, stages | 17311–17314 |
| `ShaveRemovalSelector` | size, location_group | 11300–11313 |
| `SrtSelector` | kv, ultrasound, images_present | 77436–77439 |
| `XtracSelector` | total_area | 96920–96922 |

Six procedure types go through SQL filter → LLM:
- IPL, Laser Treatment, Filler, Filler Material, Chemical Peel

---

### 2.5 Validation Engine Rules

`services/validation_engine.py` — runs after LLM coding, before modifier enforcement:

| Rule | Type | Action |
|---|---|---|
| Biopsy + shave removal on same single lesion | Hard reject | Remove secondary code, add audit flag |
| Add-on code without its primary | Hard reject | Remove add-on, add audit flag |
| Closure add-on without closure primary | Hard reject | Remove add-on, add audit flag |
| Modifier -59 without distinct lesion evidence | Soft flag | Keep code, add audit flag for human review |
| Procedure with no linked ICD-10 diagnosis | Soft flag | Keep code, add audit flag |

---

### 2.6 Prompt Architecture

All prompts use direct string building — no ChatPromptTemplate. No hardcoded CPT codes in any prompt. All codes come from selectors or SQL-retrieved candidates.

| Prompt | File | Approach |
|---|---|---|
| Clinical reader | `llm_layer/cot_prompts.py` | f-string, free-text output |
| Billing params | `llm_layer/cot_prompts.py` | `.replace()` substitution, compact JSON template |
| Focused coder | `llm_layer/cot_prompts.py` | `.replace()` substitution |
| Fallback coder | `llm_layer/coding_prompt.py` | `.replace()` substitution |
| Reasoning | `llm_layer/reasoning_prompt.py` | String concatenation |

---

### 2.7 Data Sources

| Source | Purpose |
|---|---|
| MySQL (`database/sqldb/`) | Progress notes, patients, providers |
| PostgreSQL (`database/pgdb/`) | CPT code lookup table (SQL filter queries — no vector search) |
| `data/proCodeList.csv` | Billing rules: add-on flags, laterality, charge-per-unit |
| `data/enmCodeList.csv` | E/M codes: level, time thresholds, patient type |
| `data/modifierList.csv` | Modifier definitions: enmModifier flag, descriptions |

Vector search and pgvector have been removed. PostgreSQL is used only for SQL filter queries against `cpt_embeddings` (which stores CPT codes + billing metadata from `proCodeList.csv`).

---

### 2.8 API Response Shape

`GET /api/v1/process-note/{note_id}` returns:

```json
{
  "note_id": 544084,
  "patient_summary": "...",
  "procedure": [
    {
      "cpt_code": "11102",
      "modifier": null,
      "dxcode": ["D48.5"],
      "qty": "1",
      "charge_per_unit": "No",
      "confidence": "confirmed",
      "source": "selector",
      "reasoning": {
        "justification": "...",
        "supporting_evidence": ["\"verbatim quote from note\""],
        "modifier_justification": null,
        "dx_justification": "...",
        "confidence_assessment": "supported",
        "flag": null
      }
    }
  ],
  "em": {
    "cpt_code": "99213",
    "modifier": "25",
    "dxcode": ["D48.5", "L81.4"],
    "qty": "1",
    "charge_per_unit": "Yes",
    "reasoning": { ... }
  },
  "overall_assessment": "...",
  "audit_flags": ["..."],
  "unresolved_procedures": [{"description": "...", "reason": "missing_size"}],
  "parse_source": {"biopsy_sections": "regex", "shave_removal_sections": "regex"},
  "web_refs_used": 0
}
```

---

## 3. Remaining Work

### Phase 2 — Production Hardening

| Task | File | Status |
|---|---|---|
| Structured error response from API | `app/api/route.py` | Pending |
| Audit log → PostgreSQL table | `services/audit_logger.py` | Pending (currently JSONL) |
| Token budget enforcement before LLM calls | `llm_layer/llm_client.py` | Pending |

### Phase 3 — Billing Completeness

| Task | File | Status |
|---|---|---|
| NCCI edit enforcement | `services/ncci_checker.py` | Pending |
| Payer-specific rule layer | `services/payer_filter.py` | Pending |
| Confidence router (auto_submit / review / manual) | `services/confidence_router.py` | Pending |

### Phase 4 — Observability

| Task | File | Status |
|---|---|---|
| Note quality scorer | `services/note_quality_scorer.py` | Pending |
| Accuracy dashboard queries | Needs PostgreSQL audit table | Pending |
| Correction logger (review queue) | `services/audit_logger.py` | Pending |
| CPT code freshness check (AMA addendum) | Startup check | Pending |

---

## 4. File Reference

### Core Pipeline
- `app/api/route.py` — FastAPI endpoint
- `app/graph/langgraph_builder.py` — LangGraph graph + CodingState
- `app/services/medical_engine.py` — All pipeline node implementations
- `app/services/engine_runner.py` — Graph invocation + API response formatting

### LLM Layer
- `llm_layer/llm_client.py` — OpenAI wrapper, retry logic, config-driven temperature/max_tokens
- `llm_layer/cot_prompts.py` — Clinical reader, billing params, focused coder prompts
- `llm_layer/coding_prompt.py` — Fallback one-shot coding prompt
- `llm_layer/reasoning_prompt.py` — Reasoning/audit prompt with supporting_evidence schema
- `llm_layer/note_extraction_schema.py` — Pydantic schemas for procedure extraction

### Services
- `services/clinical_parser.py` — Regex procedure extractor (15 types)
- `services/validation_engine.py` — 5 billing integrity rules (post-coding, pre-modifiers)
- `services/modifier_engine.py` — Modifier assignment driven by modifierList.csv
- `services/em_selector.py` — E/M code selection from enmCodeList.csv
- `services/mdm_classifier.py` — MDM complexity tiering (Level 2–5)
- `services/retriever.py` — SQL filter queries per procedure type
- `services/web_lookup.py` — Conditional web search (Tavily/DuckDuckGo)
- `services/reasoning_engine.py` — Per-code reasoning attachment
- `services/audit_logger.py` — JSONL audit trail
- `services/charge_lookup.py` — charge_per_unit flag from proCodeList.csv
- `services/code_selectors/` — 9 deterministic procedure selectors

### Utils
- `utils/engine_utils.py` — Enforcement passes, merge logic, E/M enforcement
- `config/config.py` — All configuration from .env (model, temperature, max_tokens, DB credentials)

---

*Last updated: 2026-05-29*
