# services/retriever.py

from database.pgdb.conn import get_db_session
from sqlalchemy import text
from loguru import logger

class CodeRetriever:

    async def search(self, query_embedding, top_k=20):

        async with get_db_session() as db:

            result = await db.execute(
                text("""
                    SELECT code_type, code, description,
                    embedding <-> :embedding AS distance
                    FROM code_embeddings
                    ORDER BY embedding <-> :embedding
                    LIMIT :k
                """),
                {
                    "embedding": str(query_embedding),
                    "k": top_k
                }
            )

            rows = result.mappings().all()
            return [dict(row) for row in rows]