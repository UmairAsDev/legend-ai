import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from sqlalchemy import text
from database.sqldb.deps import async_db_session
from loguru import logger
from database.sqldb.conn import conn, test_connection
from utils.helper import htmlparser



logger.add("logs/notes.log", rotation="10 MB")

async def notes(note_id: int):
    """Fetch a single note and attach biopsy, general, mohs, and prescription results."""
    async with async_db_session() as db:
        try:
            notes_query = text(
                """
            SELECT
                pn.noteId, pn.provider, pn.physician, pn.referringPhysician, pn.noteDate, pn.patientId,
                npn.complaints, npn.pastHistory, npn.assesment, npn.reviewofsystem, npn.currentmedication,
                npn.`procedure`, npn.biopsyNotes, npn.mohsNotes, npn.allergy, npn.examination, npn.patientSummary,
                group_concat(concat(dc.icd10Code, ' ', d.dxDescription)) AS diagnoses, pos.posName as PlaceOfService, CONCAT(p.firstName, ' ', p.lastName) as 'Rendering Provider', CONCAT(p2.firstName, ' ', p2.lastName) as 'Physician', CONCAT(p3.firstName, ' ', p3.lastName) as 'Referring Provider', CONCAT(p4.firstName, ' ', p4.lastName) as 'Billing Provider'
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
                AND pn.noteId = :note_id
            """
            )

            result = await db.execute(notes_query, {"note_id": note_id})
            notes = result.mappings().all()
            if not notes:
                return []
            notes = [dict(row) for row in notes]
            notes = htmlparser(notes)
            return notes
            
        except Exception as e:
            logger.error(f"Error fetching notes for note_id {note_id}: {e}")
            return []


if __name__ == "__main__":
    import asyncio
    
    async def main():
        try:
            # Run your logic
            data = await notes(711974)
            print(data)
        finally:
            await conn.dispose()

    asyncio.run(main())