# services/embeddings.py

import os
import asyncio
from openai import AsyncOpenAI
from loguru import logger
from sqlalchemy import text

from database.pgdb.conn import get_db_session
from services.csv_handler import CSVHandler


client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

EMBED_MODEL = "text-embedding-3-small"


class EmbeddingService:

    def __init__(self):
        self.csv = CSVHandler()

    async def generate_embedding(self, text: str):
        try:
            res = await client.embeddings.create(
                model=EMBED_MODEL,
                input=text
            )
            return res.data[0].embedding

        except Exception as e:
            logger.error(f"Embedding error: {e}")
            raise

    async def ingest_csv(self, file_path: str, code_type: str):

        # 🔹 Load correct dataset
        if code_type == "cpt":
            data = self.csv.load_cpt(file_path)

        elif code_type == "em":
            data = self.csv.load_em(file_path)

        elif code_type == "modifier":
            data = self.csv.load_modifiers(file_path)

        else:
            raise ValueError(f"Invalid code_type: {code_type}")

        if not data:
            logger.warning(f"No valid data found in {file_path}")
            return

        inserted = 0

        async with get_db_session() as db:

            for row in data:
                try:
                    code = row["code"]
                    desc = row["description"]

                    # Skip bad rows
                    if not code or code == "nan":
                        continue

                    text_data = f"{code} {desc}".strip()

                    embedding = await self.generate_embedding(text_data)

                    await db.execute(
                        text("""
                            INSERT INTO code_embeddings (code_type, code, description, embedding)
                            VALUES (:type, :code, :desc, :embedding)
                        """),
                        {
                            "type": code_type,
                            "code": code,
                            "desc": desc,
                            "embedding": str(embedding), 
                        }
                    )

                    inserted += 1

                    # optional: log every 100 rows
                    if inserted % 100 == 0:
                        logger.info(f"{inserted} rows inserted...")

                except Exception as e:
                    logger.error(f"Row failed: {row} | Error: {e}")
                    continue

        logger.success(f"Ingested {inserted} records from {file_path}")