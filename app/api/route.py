# app/api/route.py

import logging
from typing import Dict, Any

from fastapi import APIRouter, HTTPException

from app.services.engine_runner import MedicalCodingService

logger = logging.getLogger("medical_endpoint")
router = APIRouter()

service = MedicalCodingService()


@router.get("/process-note/{note_id}", response_model=Dict[str, Any])
async def process_note_endpoint(note_id: int):
    """Process a medical note and return structured coding output."""
    logger.info(f"Request received: note_id={note_id}")

    try:
        result = await service.process(note_id)
        logger.info(f"Processed note_id={note_id}")
        return result

    except ValueError as ve:
        logger.error(f"Validation error for note_id={note_id}: {ve}")
        raise HTTPException(status_code=404, detail=str(ve))

    except Exception:
        logger.exception(f"Internal error for note_id={note_id}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
