# scripts/run_migrations.py

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
from sqlalchemy import text
from database.pgdb.conn import get_db_session
from loguru import logger


SQL_STATEMENTS = [

    # -------------------------
    # Enable pgvector
    # -------------------------
    """CREATE EXTENSION IF NOT EXISTS vector""",

    # -------------------------
    # Drop existing tables (FULL RESET)
    # -------------------------
    """DROP TABLE IF EXISTS cpt_embeddings""",
    """DROP TABLE IF EXISTS em_embeddings""",
    """DROP TABLE IF EXISTS modifier_embeddings""",

    # -------------------------
    # CPT TABLE
    # -------------------------
    """
    CREATE TABLE cpt_embeddings (
        id SERIAL PRIMARY KEY,
        procode TEXT NOT NULL UNIQUE,
        codedesc TEXT,
        proname TEXT,  
        associatedwithprocode TEXT,
        minqty INT,
        maxqty INT,
        minsize TEXT,
        maxsize TEXT,
        chargeperunit FLOAT,
        embedding VECTOR(1536)
    )
    """,

    """
    CREATE INDEX idx_cpt_embedding
    ON cpt_embeddings
    USING hnsw (embedding vector_cosine_ops)
    """,

    # -------------------------
    # EM TABLE
    # -------------------------
    """
    CREATE TABLE em_embeddings (
        id SERIAL PRIMARY KEY,
        enmcode TEXT NOT NULL UNIQUE,
        enmcodedesc TEXT,
        encountertime TEXT,
        enmlevel INT,
        embedding VECTOR(1536)
    )
    """,

    """
    CREATE INDEX idx_em_embedding
    ON em_embeddings
    USING hnsw (embedding vector_cosine_ops)
    """,

    # -------------------------
    # MODIFIER TABLE
    # -------------------------
    """
    CREATE TABLE modifier_embeddings (
        id SERIAL PRIMARY KEY,
        modifier TEXT NOT NULL UNIQUE,
        modifierdesc TEXT,
        modifierdetdesc TEXT,
        embedding VECTOR(1536)
    )
    """,

    """
    CREATE INDEX idx_modifier_embedding
    ON modifier_embeddings
    USING hnsw (embedding vector_cosine_ops)
    """
]


async def run_migration():
    try:
        async with get_db_session() as db:

            logger.info("🚀 Starting migration...")

            for i, stmt in enumerate(SQL_STATEMENTS, start=1):
                try:
                    logger.info(f"➡️ Executing step {i}")
                    await db.execute(text(stmt))
                except Exception as step_error:
                    logger.error(f"❌ Step {i} failed: {step_error}")
                    raise

            # -------------------------
            # Analyze tables (query optimizer)
            # -------------------------
            logger.info("📊 Running ANALYZE on tables...")
            await db.execute(text("ANALYZE cpt_embeddings"))
            await db.execute(text("ANALYZE em_embeddings"))
            await db.execute(text("ANALYZE modifier_embeddings"))

            logger.success("✅ Migration completed successfully")

    except Exception as e:
        logger.exception(f"❌ Migration failed: {e}")
        raise


async def main():
    await run_migration()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("⚠️ Migration interrupted by user")