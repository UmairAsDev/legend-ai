# app/api/route.py

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Path
from loguru import logger

from app.services.engine_runner import MedicalCodingService

router = APIRouter()
service = MedicalCodingService()


@router.get("/process-note/{note_id}", response_model=Dict[str, Any])
async def process_note_endpoint(
    note_id: int = Path(..., gt=0, description="Positive integer note ID"),
):
    """Process a medical note and return structured coding output."""
    logger.info(f"Request received: note_id={note_id}")

    try:
        result = await service.process(note_id)
        logger.info(f"Completed: note_id={note_id}, procedures={len(result.get('procedure', []))}")
        return result

    except ValueError as ve:
        logger.warning(f"Note not found: note_id={note_id} — {ve}")
        raise HTTPException(status_code=404, detail=str(ve))

    except Exception:
        logger.exception(f"Pipeline error: note_id={note_id}")
        raise HTTPException(status_code=500, detail="Internal server error processing note")
