# app/services/medical_engine.py

from datetime import datetime
from typing import Dict, Any

from loguru import logger

from src.data_layer.progressnote import notes
from llm_layer.llm_client import LLMClient
from llm_layer.coding_prompt import build_coding_prompt

from services.embeddings import EmbeddingService
from services.retriever import CodeRetriever
from services.reranker import Reranker


# =========================
# 🔹 UTILITIES
# =========================

def serialize_data(obj):
    """
    Convert non-serializable types (datetime)
    """
    if isinstance(obj, dict):
        return {k: serialize_data(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_data(i) for i in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
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
        "allergy",
        "examination",
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
            query_text = " ".join(
                [str(v) for v in state["cleaned_note"].values() if v]
            )

            return {"query_text": query_text}

        except Exception as e:
            logger.exception(f"❌ Query creation failed: {e}")
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
            results = await self.retriever.search(state["embedding"])

            return {"candidates": results}

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
                state["query_text"]
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
                state["reranked"]
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