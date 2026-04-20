# scripts/ingest_all.py

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
from loguru import logger
from services.embeddings import EmbeddingService

BASE_DIR = Path(__file__).resolve().parent.parent


async def main():
    service = EmbeddingService()

    try:
        logger.info("🚀 Starting ingestion pipeline...")

        # =========================
        # 🔹 STEP 1: CPT
        # =========================
        logger.info("➡️ Starting CPT ingestion...")
        await service.ingest_cpt(
            str(BASE_DIR / "data" / "proCodeList.csv")
        )
        logger.success("✅ CPT ingestion completed")

        # =========================
        # 🔹 STEP 2: E/M
        # =========================
        logger.info("➡️ Starting EM ingestion...")
        await service.ingest_em(
            str(BASE_DIR / "data" / "enmCodeList.csv")
        )
        logger.success("✅ EM ingestion completed")

        # =========================
        # 🔹 STEP 3: MODIFIERS
        # =========================
        logger.info("➡️ Starting Modifier ingestion...")
        await service.ingest_modifier(
            str(BASE_DIR / "data" / "modifierList.csv")
        )
        logger.success("✅ Modifier ingestion completed")

        logger.success("🎉 All CSVs ingested successfully")

    except Exception as e:
        logger.exception(f"❌ Ingestion failed: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("⚠️ Ingestion interrupted by user")