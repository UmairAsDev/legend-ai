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
from utils.helper import htmlparser

logger.add("logs/notes.log", rotation="10 MB")


# ------------------- FETCH NOTES -------------------

async def notes(note_ids):
    """
    Fetch notes for single or multiple note_ids
    """
    if isinstance(note_ids, int):
        note_ids = [note_ids]

    async with async_db_session() as db:
        try:
            query = text(
                """
                SELECT
                    pn.noteId, pn.provider, pn.physician, pn.referringPhysician, pn.noteDate, pn.patientId,
                    npn.complaints, npn.pastHistory, npn.assesment, npn.reviewofsystem, npn.currentmedication,
                    npn.`procedure`, npn.biopsyNotes, npn.mohsNotes, npn.allergy, npn.examination, npn.patientSummary,
                    GROUP_CONCAT(CONCAT(dc.icd10Code, ' ', d.dxDescription)) AS diagnoses,
                    pos.posName as PlaceOfService,
                    CONCAT(p.firstName, ' ', p.lastName) as renderingProvider,
                    CONCAT(p2.firstName, ' ', p2.lastName) as physicianName,
                    CONCAT(p3.firstName, ' ', p3.lastName) as referringProvider,
                    CONCAT(p4.firstName, ' ', p4.lastName) as billingProvider
                FROM progressNotes pn
                LEFT JOIN providers p ON p.providerId = pn.provider 
                LEFT JOIN providers p2 ON p2.providerId = pn.physician 
                LEFT JOIN providers p3 ON p3.providerId = pn.referringPhysician
                LEFT JOIN providers p4 ON p4.providerId = pn.billingProvider 
                LEFT JOIN newProgressNotes npn ON pn.noteId = npn.noteId
                LEFT JOIN placeOfService pos ON pos.posCodes = pn.placeOfService 
                LEFT JOIN pnAssessment pa ON pa.noteId = pn.noteId
                LEFT JOIN diagnosis d ON d.dxId = pa.dxId
                LEFT JOIN diagnosisCodes dc ON dc.dxId = d.dxId AND dc.dxCodeId = pa.dxCodeId
                WHERE pn.physicianSignDate IS NOT NULL 
                AND pn.noteId IN :note_ids
                GROUP BY pn.noteId
                """
            )

            result = await db.execute(query, {"note_ids": tuple(note_ids)})
            rows = result.mappings().all()

            if not rows:
                return []

            data = [dict(r) for r in rows]
            data = htmlparser(data)

            return data

        except Exception as e:
            logger.error(f"Error fetching notes: {e}")
            return []


# ------------------- JSON STORAGE -------------------

def append_to_json(new_data, file_path="chemical_peel_notes.json"):
    """
    Append + deduplicate notes into a JSON file
    """

    try:
        # Step 1: Load existing data
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    existing_data = []
        else:
            existing_data = []

        # Step 2: Convert to dict for deduplication
        existing_map = {item["noteId"]: item for item in existing_data}

        # Step 3: Merge new data (overwrite duplicates)
        for item in new_data:
            existing_map[item["noteId"]] = item

        # Step 4: Convert back to list
        merged_data = list(existing_map.values())

        # Step 5: Save (overwrite with merged dataset)
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
            note_ids = [589167, 537933, 617395, 640908, 659708, 712861, 725845]

            data = await notes(note_ids)

            if data:
                append_to_json(data)

            print(f"Fetched {len(data)} records")

        finally:
            await conn.dispose()

    asyncio.run(main())