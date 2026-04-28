# app/services/medical_engine.py

from datetime import datetime
from decimal import Decimal
from typing import Dict, Any
import math, re

from loguru import logger

from src.data_layer.progressnote import notes
from llm_layer.llm_client import LLMClient
from llm_layer.coding_prompt import build_coding_prompt

from services.clinical_parser import ClinicalParser
from services.embeddings import EmbeddingService
from services.retriever import CodeRetriever
from services.reranker import Reranker


# =========================
# 🔹 UTILITIES
# =========================

def serialize_data(obj):
    """
    Convert non-serializable types (datetime, Decimal)
    """
    if isinstance(obj, dict):
        return {k: serialize_data(v) for k, v in obj.items()}

    elif isinstance(obj, list):
        return [serialize_data(i) for i in obj]

    elif isinstance(obj, datetime):
        return obj.isoformat()

    elif isinstance(obj, Decimal):
        return float(obj)  # 🔥 FIX

    return obj


def clean_note_data(note: Dict[str, Any]):
    """
    Keep only relevant fields for LLM
    """
    allowed_fields = [
        "complaints",
        "pastHistory",
        "assesment",
        "reviewofsystem",
        "currentmedication",
        "procedure",
        "biopsyNotes",
        "mohsNotes",
        "patientSummary",
        "diagnoses",
        "PlaceOfService",
    ]

    return {k: note.get(k) for k in allowed_fields}


def enforce_excision_quantity(parsed, llm_output):
    try:
        exc_sections = parsed.get("excision_sections", [])

        for sec in exc_sections:
            qty = sec.get("quantity", 1)

            if qty > 1:
                for cpt in llm_output["codes"]["cpt_codes"]:
                    # match malignant excision (116xx)
                    if cpt["code"].startswith("116"):
                        cpt["quantity"] = str(qty)

        return llm_output

    except Exception as e:
        logger.warning(f"⚠️ Enforcement failed: {e}")
        return llm_output
    

# =========================================================
# 🔴 CLOSURE AGGREGATION
# =========================================================
def aggregate_closures(parsed):
    logger.info("🔧 Aggregating closures...")

    grouped = {}

    for sec in parsed.get("closure_sections", []):
        key = sec.get("group_key") or f"{sec['type']}_unknown"

        grouped.setdefault(key, {
            "type": sec["type"],
            "group_key": key,
            "total_size": 0.0,
            "locations": []
        })

        grouped[key]["total_size"] += float(sec.get("size") or 0)
        grouped[key]["locations"].append(sec.get("location"))

    parsed["closure_aggregated"] = list(grouped.values())

    # 🔴 CRITICAL DEBUG LOG
    logger.info("🧾 ===== CLOSURE DEBUG =====")

    for sec in parsed.get("closure_sections", []):
        logger.info(
            f"RAW → size={sec['size']} | loc={sec['location']} | type={sec['type']}"
        )

    for g in parsed["closure_aggregated"]:
        logger.info(
            f"AGG → group={g['group_key']} | total={g['total_size']} | locs={g['locations']}"
        )

    logger.info("🧾 ==========================")

    return parsed


# =========================================================
# 🔴 GENERIC ADD-ON ENGINE
# =========================================================
def build_closure_hierarchy(candidates):
    hierarchy = {}

    for c in candidates:
        parent = str(c.get("associatedWithProCode")).strip() if c.get("associatedWithProCode") else None
        if parent:
            hierarchy.setdefault(parent, []).append(c)

    return hierarchy


def select_primary_code(candidates, total_size):
    
    candidates = sorted(
    candidates,
    key=lambda x: float(x.get("minSize") or 0)
    )

    for c in candidates:
        if c.get("associatedWithProCode"):
            continue

        min_s = float(c.get("minSize") or 0)
        max_s = float(c.get("maxSize") or 999)

        if min_s <= total_size <= max_s:
            return c

    return None


def calculate_addon_units(addon_code, total_size, base_max):
    extra = total_size - base_max
    if extra <= 0:
        return 0

    desc = (addon_code.get("description") or "").lower()
    match = re.search(r"each additional (\d+\.?\d*)", desc)

    step = float(match.group(1)) if match else 5

    return math.ceil(extra / step)


def enforce_closure_addon(parsed, candidates, llm_output):
    try:
        logger.info(f"📊 Parsed aggregated closures: {parsed.get('closure_aggregated')}")
        logger.info("🔧 Enforcing closure add-ons (GENERIC)...")

        closure_groups = parsed.get("closure_aggregated", [])
        if not closure_groups:
            return llm_output

        closure_candidates = [
            c for c in candidates
            if str(c.get("code", "")).startswith(("120", "131"))
        ]

        hierarchy = build_closure_hierarchy(closure_candidates)

        final_codes = []

        for group in closure_groups:
            total_size = group["total_size"]
            ctype = group["type"]

            logger.info(f"📏 Closure group → size={total_size}, type={ctype}")

            location_group = group.get("group_key", "").split("_")[-1]
            type_candidates = [
                c for c in closure_candidates
                if (
                    (ctype == "complex" and str(c["code"]).startswith("131")) or
                    (ctype == "intermediate" and str(c["code"]).startswith("120"))
                )
            ]

            # 🔴 FILTER BY LOCATION FAMILY (CRITICAL)
            filtered_candidates = []

            for c in type_candidates:
                desc = (c.get("description") or "").lower()

                if location_group == "extremities":
                    if not any(k in desc for k in ["scalp", "arm", "leg"]):
                        continue

                elif location_group == "critical":
                    if not any(k in desc for k in ["nose", "lip", "ear", "eyelid"]):
                        continue

                elif location_group == "high_risk":
                    if not any(k in desc for k in ["face", "hand", "foot", "neck", "chin", "cheek"]):
                        continue

                elif location_group == "trunk":
                    if not any(k in desc for k in ["trunk", "back", "chest", "abdomen"]):
                        continue

                filtered_candidates.append(c)

            type_candidates = filtered_candidates

            primary = select_primary_code(type_candidates, total_size)

            if not primary:
                logger.warning("⚠️ No primary closure match")
                continue

            primary_code = str(primary["code"])
            base_max = float(primary.get("maxSize") or 0)

            final_codes.append({
                "code": primary_code,
                "description": primary["description"],
                "modifier": None,
                "linked_dx": [],
                "quantity": "1"
            })

            for addon in hierarchy.get(primary_code, []):
                units = calculate_addon_units(addon, total_size, base_max)

                if units > 0:
                    final_codes.append({
                        "code": addon["code"],
                        "description": addon["description"],
                        "modifier": None,
                        "linked_dx": [],
                        "quantity": str(units)
                    })

        # 🔴 REMOVE WRONG LLM CLOSURES
        llm_output["codes"]["cpt_codes"] = [
            c for c in llm_output["codes"]["cpt_codes"]
            if not str(c["code"]).startswith(("120", "131"))
        ]

        llm_output["codes"]["cpt_codes"].extend(final_codes)

        logger.info(f"✅ Final closure codes: {final_codes}")

        return llm_output

    except Exception as e:
        logger.exception(f"❌ Closure enforcement failed: {e}")
        return llm_output
    
# =========================
# 🔹 NODE CLASS (LangGraph)
# =========================

class CodingNodes:

    def __init__(self):
        self.embedder = EmbeddingService()
        self.parser = ClinicalParser()
        self.retriever = CodeRetriever()
        self.reranker = Reranker()
        self.llm = LLMClient()

    # -------------------------
    # 🔹 FETCH NOTE
    # -------------------------
    async def fetch(self, state):
        try:
            logger.info(f"📥 Fetching note: {state['note_id']}")

            data = await notes(state["note_id"])

            if not data:
                raise ValueError("Note not found")

            return {"raw_note": data[0]}

        except Exception as e:
            logger.exception(f"❌ Fetch failed: {e}")
            raise

    # -------------------------
    # 🔹 CLEAN NOTE
    # -------------------------
    async def clean(self, state):
        try:
            cleaned = clean_note_data(state["raw_note"])
            cleaned = serialize_data(cleaned)

            return {"cleaned_note": cleaned}

        except Exception as e:
            logger.exception(f"❌ Clean step failed: {e}")
            raise

    # -------------------------
    # 🔹 BUILD QUERY
    # -------------------------
    async def query(self, state):
        try:
            query_parts = []
            for k, v in state["cleaned_note"].items():
                if v:
                    query_parts.append(f"{k}: {v}")

            query_text = " | ".join(query_parts)

            return {"query_text": query_text}

        except Exception as e:
            logger.exception(f"❌ Query creation failed: {e}")
            raise


    # -------------------------
    # 🔹 PARSER
    # -------------------------
    async def parse(self, state):
        try:
            parsed = self.parser.parse(state["cleaned_note"])
            parsed = aggregate_closures(parsed)
            logger.info(f"🧾 biopsyNotes AFTER CLEAN: {state['cleaned_note'].get('biopsyNotes')}")
            return {"parsed": parsed}
        except Exception as e:
            raise

    # -------------------------
    # 🔹 EMBEDDING
    # -------------------------
    async def embed(self, state):
        try:
            embeddings = await self.embedder.generate_embeddings_batch(
                [state["query_text"]]
            )

            return {"embedding": embeddings[0]}

        except Exception as e:
            logger.exception(f"❌ Embedding failed: {e}")
            raise

    # -------------------------
    # 🔹 RETRIEVE
    # -------------------------
    async def retrieve(self, state):
        try:
            parsed = state.get("parsed", {})
            cleaned = state.get("cleaned_note", {})

            logger.info(f"🧠 Retrieval Decision | Parsed: {parsed}")

            all_candidates = []

            # -------------------------
            # 🔴 EXCISION (FIRST PRIORITY BUT NOT EXCLUSIVE)
            # -------------------------
            if parsed.get("has_excision"):
                logger.info("🔴 EXCISION DETECTED")

                for sec in parsed.get("excision_sections", []):
                    size = sec.get("size")
                    text = sec.get("text", "")

                    # 🔹 Extract location
                    loc_match = re.search(r"Location:\s*(.*)", text)
                    location = loc_match.group(1).strip() if loc_match else ""

                    logger.info(f"📌 Excision Section → size={size}, location={location}")

                    if size:
                        res = await self.retriever.excision_filter(size, location)
                        logger.info(f"   ↳ Retrieved {len(res)} excision candidates")
                        all_candidates.extend(res)
                    else:
                        logger.warning("⚠️ Excision section missing size → skipped")

            # -------------------------
            # 🔴 BIOPSY (ALWAYS ADD IF PRESENT)
            # -------------------------
            if parsed.get("has_biopsy"):
                logger.info("🔴 BIOPSY DETECTED")

                biopsy = await self.retriever.biopsy_filter()
                logger.info(f"   ↳ Retrieved {len(biopsy)} biopsy candidates")

                all_candidates.extend(biopsy)

            # -------------------------
            # 🔴 MOHS (FINAL SAFE VERSION)
            # -------------------------
            if parsed.get("has_mohs"):
                logger.info("🔴 MOHS DETECTED")
                logger.info(f"🧠 Mohs Sections: {parsed.get('mohs_sections')}")

                for sec in parsed.get("mohs_sections", []):
                    location = sec.get("location", "")

                    if not location:
                        logger.error("❌ Mohs location missing → fallback mode")

                    logger.info(f"📌 Mohs → location={location}")

                    res = await self.retriever.mohs_filter(location) or []

                    logger.info(f"   ↳ Mohs candidates: {len(res)}")

                    # tag source
                    for r in res:
                        r["source"] = "mohs"

                    all_candidates.extend(res)


            # -------------------------
            # 🔴 CLOSURE
            # -------------------------
            if parsed.get("has_closure"):

                logger.info("🔴 CLOSURE DETECTED (AGGREGATED MODE)")

                for group in parsed.get("closure_aggregated", []):
                    size = group.get("total_size")
                    ctype = group.get("type")
                    group_key = group.get("group_key")

                    logger.info(
                        f"📊 Closure Aggregated → group={group_key}, "
                        f"type={ctype}, total_size={size}"
                    )

                    if size:
                        location_group = group.get("group_key", "").split("_")[-1]
                        res = await self.retriever.closure_filter(size, location_group, ctype)

                        for r in res:
                            r["source"] = "closure"
                            r["closure_group"] = group_key  # 🔥 important for debugging

                        logger.info(f"   ↳ Retrieved {len(res)} closure candidates")

                        all_candidates.extend(res)
                    else:
                        logger.warning("⚠️ Closure missing size → skipped")

            # -------------------------
            # 🔴 IF ANY PROCEDURAL CODES FOUND → RETURN
            # -------------------------
            if all_candidates:
                # 🔹 Deduplicate (important)
                unique = {}
                for c in all_candidates:
                    key = (c.get("code"), c.get("type"), c.get("source"), c.get("location"))
                    if key not in unique:
                        unique[key] = c

                final_candidates = list(unique.values())

                logger.info(f"✅ Total combined candidates (deduped): {len(final_candidates)}")

                return {"candidates": final_candidates}

            # -------------------------
            # 🟡 PROCEDURE-BASED FALLBACK (SEMANTIC)
            # -------------------------
            if parsed.get("has_procedure"):
                logger.info("🟡 PROCEDURE DETECTED → semantic search")

                embedding = state["embedding"]

                candidates = await self.retriever.search(embedding)

                logger.info(f"⚠️ Procedure-based candidates: {len(candidates)}")

                return {"candidates": candidates}

            # -------------------------
            # ⚠️ FINAL FALLBACK
            # -------------------------
            logger.warning("⚠️ FALLBACK SEARCH TRIGGERED")

            candidates = await self.retriever.search(state["embedding"])

            logger.info(f"⚠️ Fallback candidates: {len(candidates)}")

            return {"candidates": candidates}

        except Exception as e:
            logger.exception(f"❌ Retrieval failed: {e}")
            raise


    # -------------------------
    # 🔹 RERANK
    # -------------------------
    async def rerank(self, state):
        try:
            ranked = self.reranker.rerank(
            state["candidates"],
            state
        )
            return {"reranked": ranked}

        except Exception as e:
            logger.exception(f"❌ Rerank failed: {e}")
            raise

    # -------------------------
    # 🔹 LLM CALL (JsonOutputParser)
    # -------------------------
    async def llm_call(self, state):
        try:
            logger.info("🧠 Calling LLM with structured parser")

            # 🔹 Get prompt + parser
            prompt, parser, formatted_prompt = build_coding_prompt(
                {
                    "note": state["cleaned_note"],
                    "parsed": state["parsed"]   # ✅ CRITICAL
                },
                state["candidates"]
            )

            # 🔹 LLM call with parser
            result = await self.llm.generate_response(
                formatted_prompt,
                parser=parser
            )

            # 🔴 HARD FIX (GUARANTEES CORRECT OUTPUT)
            result = enforce_excision_quantity(state["parsed"], result)
            result = enforce_closure_addon(
                state["parsed"],
                state["candidates"],   # 🔥 REQUIRED (comes from retrieve step)
                result
            )

            return {"llm_output": result}

        except Exception as e:
            logger.exception(f"❌ LLM step failed: {e}")
            raise