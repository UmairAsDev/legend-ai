# services/embeddings.py

import os
import asyncio
from typing import List, Dict

from openai import AsyncOpenAI
from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.pgdb.conn import get_db_session
from database.pgdb.models import (
    CPTEmbedding,
    EMEmbedding,
    ModifierEmbedding,
)
from services.csv_handler import CSVHandler


client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EMBED_MODEL = "text-embedding-3-small"
BATCH_SIZE = 50


class EmbeddingService:

    def __init__(self):
        self.csv = CSVHandler()

    # =========================
    # 🔹 EMBEDDING GENERATION
    # =========================
    async def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        for attempt in range(3):
            try:
                res = await client.embeddings.create(
                    model=EMBED_MODEL,
                    input=texts
                )
                return [d.embedding for d in res.data]

            except Exception as e:
                logger.warning(f"⚠️ Retry {attempt + 1} due to: {e}")
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError("❌ Embedding failed after retries")

    # =========================
    # 🔹 CPT INGESTION
    # =========================
    async def ingest_cpt(self, file_path: str):
        data = self.csv.load_cpt(file_path)

        if not data:
            logger.warning(f"No CPT data found: {file_path}")
            return

        inserted = 0

        async with get_db_session() as db:
            try:
                logger.info(f"📥 Ingesting CPT: {len(data)} rows")

                for i in range(0, len(data), BATCH_SIZE):
                    batch = data[i:i + BATCH_SIZE]

                    texts = [
                        f"{r['proCode']} {r.get('codeDesc') or ''} {r.get('proName') or ''}".strip()
                        for r in batch
                    ]

                    embeddings = await self.generate_embeddings_batch(texts)

                    batch_params: List[Dict] = []

                    for row, emb in zip(batch, embeddings):
                        batch_params.append({
                            "pro_code": row["proCode"],
                            "code_desc": row.get("codeDesc"),
                            "pro_name": row.get("proName"),
                            "associated_with_pro_code": row.get("associatedWithProCode"),
                            "min_qty": row.get("minQty"),
                            "max_qty": row.get("maxQty"),
                            "min_size": row.get("minSize"),
                            "max_size": row.get("maxSize"),
                            "charge_per_unit": row.get("chargePerUnit"),
                            "embedding": emb
                        })

                    stmt = pg_insert(CPTEmbedding).values(batch_params)

                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["procode"]  # DB column name
                    )

                    await db.execute(stmt)
                    await db.commit()

                    inserted += len(batch_params)
                    logger.info(f"📊 CPT progress: {inserted}/{len(data)}")

                logger.success(f"✅ CPT ingestion complete: {inserted} rows")

            except Exception as e:
                logger.exception(f"❌ CPT ingestion failed: {e}")
                raise

    # =========================
    # 🔹 E/M INGESTION
    # =========================
    async def ingest_em(self, file_path: str):
        data = self.csv.load_em(file_path)

        if not data:
            logger.warning(f"No EM data found: {file_path}")
            return

        inserted = 0

        async with get_db_session() as db:
            try:
                logger.info(f"📥 Ingesting EM: {len(data)} rows")

                for i in range(0, len(data), BATCH_SIZE):
                    batch = data[i:i + BATCH_SIZE]

                    texts = [
                        f"{r['enmCode']} {r.get('enmCodeDesc') or ''}".strip()
                        for r in batch
                    ]

                    embeddings = await self.generate_embeddings_batch(texts)

                    batch_params = []

                    for row, emb in zip(batch, embeddings):
                        batch_params.append({
                            "enm_code": row["enmCode"],
                            "enm_code_desc": row.get("enmCodeDesc"),
                            "encounter_time": row.get("encounterTime"),
                            "enm_level": row.get("enmLevel"),
                            "embedding": emb
                        })

                    stmt = pg_insert(EMEmbedding).values(batch_params)

                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["enmcode"]
                    )

                    await db.execute(stmt)
                    await db.commit()

                    inserted += len(batch_params)
                    logger.info(f"📊 EM progress: {inserted}/{len(data)}")

                logger.success(f"✅ EM ingestion complete: {inserted} rows")

            except Exception as e:
                logger.exception(f"❌ EM ingestion failed: {e}")
                raise

    # =========================
    # 🔹 MODIFIER INGESTION
    # =========================
    async def ingest_modifier(self, file_path: str):
        data = self.csv.load_modifiers(file_path)

        if not data:
            logger.warning(f"No Modifier data found: {file_path}")
            return

        inserted = 0

        async with get_db_session() as db:
            try:
                logger.info(f"📥 Ingesting Modifiers: {len(data)} rows")

                for i in range(0, len(data), BATCH_SIZE):
                    batch = data[i:i + BATCH_SIZE]

                    texts = [
                        f"{r['modifier']} {r.get('modifierDesc') or ''}".strip()
                        for r in batch
                    ]

                    embeddings = await self.generate_embeddings_batch(texts)

                    batch_params = []

                    for row, emb in zip(batch, embeddings):
                        batch_params.append({
                            "modifier": row["modifier"],
                            "modifier_desc": row.get("modifierDesc"),
                            "modifier_det_desc": row.get("modifierDetDesc"),
                            "embedding": emb
                        })

                    stmt = pg_insert(ModifierEmbedding).values(batch_params)

                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["modifier"]
                    )

                    await db.execute(stmt)
                    await db.commit()

                    inserted += len(batch_params)
                    logger.info(f"📊 Modifier progress: {inserted}/{len(data)}")

                logger.success(f"✅ Modifier ingestion complete: {inserted} rows")

            except Exception as e:
                logger.exception(f"❌ Modifier ingestion failed: {e}")
                raise