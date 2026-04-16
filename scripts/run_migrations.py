# scripts/run_migrations.py

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
from sqlalchemy import text
from database.pgdb.conn import get_db_session
from loguru import logger


SQL_STATEMENTS = [

    # Enable extension
    """CREATE EXTENSION IF NOT EXISTS vector""",

    # Drop old table (important)
    """DROP TABLE IF EXISTS code_embeddings""",

    # Create new table with 1536 dims
    """
    CREATE TABLE code_embeddings (
        id SERIAL PRIMARY KEY,
        code_type TEXT,
        code TEXT,
        description TEXT,
        embedding VECTOR(1536)
    )
    """,

    # Create index (now valid)
    """
    CREATE INDEX idx_embedding
    ON code_embeddings
    USING hnsw (embedding vector_cosine_ops)
    """
]


async def run_migration():
    try:
        async with get_db_session() as db:

            logger.info("Starting migration...")

            for i, stmt in enumerate(SQL_STATEMENTS, start=1):
                logger.info(f"Executing step {i}...")
                await db.execute(text(stmt))

            await db.execute(text("ANALYZE code_embeddings"))

            logger.success("Migration completed successfully ✅")

    except Exception as e:
        logger.error(f"Migration failed ❌: {e}")
        raise


async def main():
    await run_migration()


if __name__ == "__main__":
    asyncio.run(main())