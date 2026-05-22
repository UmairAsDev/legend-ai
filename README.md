# AI Medical Coding Engine

AI-powered dermatology medical coding pipeline that extracts structured procedural information from clinical notes and assigns CPT, E/M, and modifier codes using deterministic parsing, vector retrieval, rule-based filtering, and LLM reasoning.

---

## Overview

This system processes dermatology progress notes and automatically:

1. Cleans and normalizes note data
2. Parses procedure-specific clinical sections
3. Extracts structured procedural attributes
4. Retrieves CPT/E/M/modifier candidates from pgvector
5. Applies deterministic procedural filtering
6. Sends constrained candidates to the LLM
7. Produces structured coding output

The architecture combines:

* Deterministic medical parsing
* Retrieval-Augmented Generation (RAG)
* Vector search with pgvector
* LangGraph workflow orchestration
* Rule-based CPT validation
* LLM constrained coding generation

---

## Architecture

```text
                ┌────────────────────┐
                │   Progress Notes   │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │  HTML Cleaning     │
                │ helper.py/parser.py│
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Clinical Parser    │
                │ clinical_parser.py │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Structured Parsed  │
                │ Procedure Sections │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Embedding Service  │
                │ embeddings.py      │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ pgvector Retrieval │
                │ retriever.py       │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Rule-based Filters │
                │ Location/Size/etc  │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ Coding Prompt      │
                │ coding_prompt.py   │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ GPT-4o             │
                │ Structured Coding  │
                └─────────┬──────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ JSON Output        │
                │ CPT/E&M/Modifiers  │
                └────────────────────┘
```

---

## Core Components

## 1. Clinical Parser

File:

* `clinical_parser.py`

Responsible for:

* Detecting procedures
* Extracting structured attributes
* Splitting multi-site procedures
* Normalizing procedural logic

Examples:

* Mohs
* Excision
* Destruction
* Chemical Peel
* IPL
* Shave Removal
* Laser Treatment

Extraction includes:

* Location
* Size
* Quantity
* Method
* Choice
* Stages
* Area treated
* Anatomical grouping

---

## 2. Parser Utilities

File:

* `parser_utils.py`

Contains:

* Shared regex utilities
* Keyword dictionaries
* Anatomical mappings
* Procedure keyword maps
* Size extraction helpers
* Mohs stage extraction
* Chemical peel mappings
* IPL mappings

Acts as the deterministic rule engine foundation.

---

## 3. Embedding Pipeline

Files:

* `embeddings.py`
* `csv_handler.py`

Responsibilities:

* Load CPT/EM/modifier CSVs
* Generate OpenAI embeddings
* Store embeddings in PostgreSQL pgvector

Embedding Model:

* `text-embedding-3-small`

Stored Tables:

* `cpt_embeddings`
* `em_embeddings`
* `modifier_embeddings`

---

## Vector Retrieval Layer

File:

* `retriever.py`

Uses:

* pgvector similarity search
* deterministic CPT filtering

Includes custom procedural filters for:

* Mohs
* Excision
* Destruction
* IPL
* Laser Treatment
* Chemical Peel
* Debridement
* XTRAC
* Shave Removal

Filtering logic includes:

* anatomical mapping
* size range validation
* quantity ranges
* location grouping
* method matching
* choice matching
* add-on CPT logic

---

## LangGraph Workflow

Files:

* `medical_engine.py`
* `langgraph_builder.py`
* `engine_runner.py`

Pipeline:

```text
FETCH NOTE
    ↓
CLEAN NOTE
    ↓
BUILD QUERY
    ↓
PARSE PROCEDURES
    ↓
GENERATE EMBEDDING
    ↓
RETRIEVE CANDIDATES
    ↓
LLM CODING
```

Each stage is modular and independently extensible.

---

## LLM Layer

Files:

* `coding_prompt.py`
* `llm_client.py`

Responsibilities:

* Build structured coding instructions
* Constrain hallucinations
* Enforce CPT grouping rules
* Apply procedure-specific logic

Current model:

* GPT-4o

Output format:

* strict JSON schema

Includes:

* CPT codes
* E/M codes
* modifiers
* linked ICDs
* justification
* quantities

---

## API Layer

Files:

* `route.py`
* `main.py`

Framework:

* FastAPI

Endpoint:

```http
GET /api/v1/process-note/{note_id}
```

Response:

```json
{
  "note_id": 123,
  "retrieved_candidates": [],
  "reranked_codes": [],
  "llm_output": {}
}
```

---

## Database Layer

Files:

* `models.py`
* `run_migrations.py`

Database:

* PostgreSQL
* pgvector

Vector Index:

* HNSW cosine similarity

Tables:

* CPT embeddings
* EM embeddings
* Modifier embeddings

---

## Implemented Procedure Series

## Completed

### Skin Procedures

* Biopsy
* Mohs
* Excision Benign Lesion & Margins
* Excision Malignant Lesion & Margins
* Shave Removal
* Destruction Benign Lesions
* Destruction Malignant Lesions

### Repair Procedures

* Closures
* Adjacent Tissue Transfer

### Radiation

* Surface Radiation Therapy (SRT)

### Wound Care

* Debridement

### Cosmetic / Aesthetic

* Laser Treatment
* Intense Pulsed Light (IPL)
* Filler Material
* Filler
* Chemical Peel
* Chemical Peel Dermal
* Chemical Peel Epidermal

---

## Pending Procedure Series

## Planned

* Implant (Autologous)
* Human Cadaver-Derived Implant
* Semi-Permanent Bio-Catalyst Filler
* Injectable Micro-Implant
* Excision Non Skin
* Soft Tissue Excision
* Therapeutic Radiology Treatment Planning
* Reflectance Confocal Microscopy
* Immunotherapy Injections

---

## Procedural Design Philosophy

The system intentionally uses:

## Deterministic Parsing First

LLMs are NOT trusted for:

* procedural extraction
* size calculations
* anatomical grouping
* add-on logic
* quantity validation

These are handled via:

* regex
* rule engines
* procedural filters

---

## LLMs Used Only For

* final coding reasoning
* ICD linking
* justification
* constrained CPT selection

This minimizes hallucinations and improves billing safety.

---

## Important Engineering Patterns

## 1. Multi-Site Isolation

Each lesion/site/procedure is processed independently.

## 2. Deterministic Anatomical Mapping

Location groups:

* face
* special sites
* trunk/extremities

## 3. CPT Boundary Validation

Exact size ranges are enforced.

## 4. Add-on CPT Handling

Associated CPT logic handled deterministically.

## 5. Structured Retrieval

LLM only receives constrained candidates.

---

## Technologies

## Backend

* FastAPI
* LangGraph
* SQLAlchemy
* PostgreSQL
* pgvector

## AI/ML

* OpenAI GPT-4o
* OpenAI Embeddings
* LangChain

## Utilities

* Loguru
* BeautifulSoup
* Regex-driven parsing

---

## Current System Strengths

* Highly deterministic
* Strong procedural parsing
* Modular architecture
* Procedure-specific retrieval
* Minimal hallucination risk
* Extensible CPT series design
* Production-oriented logging
* Vector-based retrieval
* Structured JSON outputs

---

## Key Files

| File                 | Responsibility           |
| -------------------- | ------------------------ |
| clinical_parser.py   | Clinical extraction      |
| parser_utils.py      | Shared parsing utilities |
| retriever.py         | CPT retrieval/filtering  |
| embeddings.py        | Embedding generation     |
| coding_prompt.py     | LLM constraints          |
| medical_engine.py    | Main orchestration       |
| langgraph_builder.py | Workflow graph           |
| llm_client.py        | GPT interaction          |
| models.py            | DB schema                |
| ingest_all.py        | CSV ingestion            |
| run_migrations.py    | DB setup                 |
| main.py              | FastAPI app              |

---

## Project Status

Current status:

* Production-grade dermatology coding engine foundation completed
* Multi-procedure parsing operational
* pgvector retrieval operational
* LangGraph orchestration operational
* GPT-constrained coding operational
* Additional procedure series pending implementation
