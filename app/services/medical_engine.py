# app/services/medical_engine.py
import re
from loguru import logger

from src.data_layer.progressnote import notes
from llm_layer.llm_client import LLMClient
from llm_layer.coding_prompt import build_coding_prompt

from services.clinical_parser import ClinicalParser
from services.embeddings import EmbeddingService
from services.retriever import CodeRetriever
from services.reranker import Reranker

from utils.engine_utils import (
    serialize_data, clean_note_data, 
    enforce_closure_addon, enforce_excision_quantity,
    aggregate_closures
)

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
            # 🔴 SRT PROCEDURE
            # -------------------------
            if parsed.get("has_srt"):
                logger.info("🔴 SRT DETECTED")

                for sec in parsed.get("srt_sections", []):

                    logger.info(
                        f"🧠 SRT Decision → kv={sec.get('kv')} | "
                        f"ultrasound={sec.get('ultrasound')} | "
                        f"images_present={sec.get('images_present')}"
                    )

                    res = await self.retriever.srt_filter(sec)

                    for r in res:
                        r["source"] = "srt"

                    all_candidates.extend(res)  

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