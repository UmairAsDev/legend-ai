# llm_layer/medical_engine.py
"""
End-to-end medical coding pipeline:
DB → clean → LLM → safe JSON output
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Any

from llm_layer.llm_client import LLMClient
from llm_layer.coding_prompt import build_coding_prompt
from database.sqldb.conn import conn
from src.data_layer.progressnote import notes

from services.embeddings import EmbeddingService
from services.retriever import CodeRetriever
from services.reranker import Reranker

# ---------------- UTILITIES ---------------- #

def serialize_data(obj: Any) -> Any:
    """
    Convert datetime and non-serializable objects safely.
    """

    if isinstance(obj, dict):
        return {k: serialize_data(v) for k, v in obj.items()}

    elif isinstance(obj, list):
        return [serialize_data(i) for i in obj]

    elif isinstance(obj, datetime):
        return obj.isoformat()

    return obj


def clean_note_data(note: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove noisy fields for better LLM accuracy.
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


def safe_json_parse(text: str) -> Dict[str, Any]:
    """
    Robust JSON parser for LLM output.
    """

    if not text:
        raise ValueError("Empty LLM response")

    text = text.strip()

    # Extract JSON block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in LLM output")

    return json.loads(match.group(0))


# ---------------- ENGINE ---------------- #

class MedicalCodingEngine:

    def __init__(self):
        self.llm = LLMClient()
        self.embedder = EmbeddingService()
        self.retriever = CodeRetriever()
        self.reranker = Reranker()
    try:

        async def process_note(self, note_id: int):

            note_data = await notes(note_id)
            note_data = clean_note_data(note_data[0])
            note_data = serialize_data(note_data)

            # 🔹 Step: create query text
            query_text = " ".join([str(v) for v in note_data if v])

            # 🔹 Step: embedding
            query_embedding = await self.embedder.generate_embedding(query_text)

            # 🔹 Step: retrieve
            candidates = await self.retriever.search(query_embedding, top_k=20)

            # 🔹 Step: rerank
            top_codes = self.reranker.rerank(candidates, query_text)

            # 🔹 Step: prompt with context
            prompt = build_coding_prompt(note_data, top_codes)

            response = await self.llm.generate_response(prompt)

            parsed = safe_json_parse(response)

            return {
                "note_id": note_id,
                "retrieved_codes": top_codes,
                "llm_output": parsed
            }

    except Exception as e:
            raise RuntimeError(f"Processing failed: {e}")
        


# ---------------- RUNNER ---------------- #

async def main():
    try:
        engine = MedicalCodingEngine()

        result = await engine.process_note(691139)

    finally:
        await conn.dispose()

    print("\n===== INPUT DATA =====\n")
    print(json.dumps(result["input_data"], indent=2))

    print("\n===== LLM OUTPUT =====\n")
    print(json.dumps(result["llm_output"], indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped safely.")