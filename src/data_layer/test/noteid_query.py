# src/data_layer/progressnote.py

import sys
from pathlib import Path
import json
import os

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from sqlalchemy import text
from database.sqldb.deps import async_db_session
from loguru import logger
from database.sqldb.conn import conn

logger.add("logs/notes.log", rotation="10 MB")


# ------------------- FETCH NOTES -------------------

async def notes():
    """
    Fetch notes based on biopsyNotes conditions
    """
    async with async_db_session() as db:
        try:
            query = text(
                """
                SELECT 
                    npn.noteId, 
                    npn.noteDate
                FROM newProgressNotes npn
                WHERE 
                    (npn.mohsNotes LIKE '%Intense Pulsed Light%')
                    OR (npn.biopsyNotes LIKE '%Intense Pulsed Light%')
                    OR (npn.procedure LIKE '%Intense Pulsed Light%')
                    AND (npn.procedure LIKE '%sq%')
                    AND npn.noteDate > '2020-01-01'
                """
            )

            result = await db.execute(query)
            rows = result.mappings().all()

            if not rows:
                return []

            return [dict(r) for r in rows]

        except Exception as e:
            logger.error(f"Error fetching notes: {e}")
            return []


# ------------------- JSON STORAGE -------------------

def append_to_json(new_data, file_path="IPL_wom_id.json"):
    """
    Append + deduplicate notes into a JSON file
    """
    try:
        # Load existing data
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    existing_data = []
        else:
            existing_data = []

        # Deduplicate using noteId
        existing_map = {item["noteId"]: item for item in existing_data}

        # Merge new data
        for item in new_data:
            existing_map[item["noteId"]] = item

        merged_data = list(existing_map.values())

        # Save updated data
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(merged_data, f, indent=2, default=str)

        logger.info(f"{len(new_data)} records processed | Total stored: {len(merged_data)}")

    except Exception as e:
        logger.error(f"Error writing JSON: {e}")


# ------------------- MAIN -------------------

if __name__ == "__main__":
    import asyncio

    async def main():
        try:
            data = await notes()

            if data:
                append_to_json(data)

            print(f"Fetched {len(data)} records")

        finally:
            await conn.dispose()

    asyncio.run(main())