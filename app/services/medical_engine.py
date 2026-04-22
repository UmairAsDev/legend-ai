# app/services/medical_engine.py

from datetime import datetime
from decimal import Decimal
from typing import Dict, Any

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

            # -------------------------
            # 🔴 STRICT ROUTING
            # -------------------------

            # 🔹 CASE 1: BOTH BIOPSY + MOHS
            if parsed.get("has_biopsy") and parsed.get("has_mohs"):
                logger.info("🔴 BOTH BIOPSY + MOHS DETECTED")

                biopsy = await self.retriever.biopsy_filter()
                mohs = await self.retriever.mohs_filter()
                candidates = biopsy + mohs

                logger.info(f"✅ Biopsy: {len(biopsy)} | Mohs: {len(mohs)}")
                return {"candidates": candidates}

            # 🔹 CASE 2: ONLY BIOPSY
            if parsed.get("has_biopsy"):
                logger.info("🔴 ONLY BIOPSY DETECTED")

                candidates = await self.retriever.biopsy_filter()

                logger.info(f"✅ Biopsy Candidates: {len(candidates)}")
                return {"candidates": candidates}

            # 🔹 CASE 3: ONLY MOHS
            if parsed.get("has_mohs"):
                logger.info("🔴 ONLY MOHS DETECTED")

                candidates = await self.retriever.mohs_filter()

                logger.info(f"✅ Mohs Candidates: {len(candidates)}")
                return {"candidates": candidates}

            # 🔹 CASE 4: PROCEDURE-BASED SEARCH
            if parsed.get("has_procedure"):
                logger.info("🟡 PROCEDURE DETECTED → semantic search")

                embedding = state["embedding"]

                candidates = await self.retriever.search(embedding)

                logger.info(f"⚠️ Procedure-based candidates: {len(candidates)}")
                return {"candidates": candidates}

            # 🔹 CASE 5: FALLBACK
            logger.warning("⚠️ FALLBACK SEARCH TRIGGERED")

            candidates = await self.retriever.search(state["embedding"])

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
                state["cleaned_note"],
                state["candidates"]
            )

            # 🔹 LLM call with parser
            result = await self.llm.generate_response(
                formatted_prompt,
                parser=parser
            )

            return {"llm_output": result}

        except Exception as e:
            logger.exception(f"❌ LLM step failed: {e}")
            raise