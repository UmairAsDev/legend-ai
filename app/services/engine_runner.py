# app/services/engine_runner.py

from loguru import logger
from app.graph.langgraph_builder import build_graph


class MedicalCodingService:

    def __init__(self):
        self.graph = build_graph()

    async def process(self, note_id: int):
        try:
            logger.info(f"🚀 Running LangGraph pipeline for note {note_id}")

            result = await self.graph.ainvoke({
                "note_id": note_id
            })

            return {
                "note_id": note_id,
                "retrieved_candidates": result.get("candidates"),
                "reranked_codes": result.get("reranked"),
                "llm_output": result.get("llm_output")
            }

        except Exception as e:
            logger.exception(f"❌ Pipeline failed: {e}")
            raise