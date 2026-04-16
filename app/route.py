# app/route.py
"""
api/endpoints/medical_coding.py

FastAPI endpoint for medical coding engine.
"""

import logging
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from llm_layer.medical_engine import MedicalCodingEngine

logger = logging.getLogger("medical_endpoint")
logger.setLevel(logging.INFO)

router = APIRouter()

engine = MedicalCodingEngine()

@router.get("/process-note/{note_id}", response_model=Dict[str, Any])
async def process_note_endpoint(note_id: int):
    """
    Process a medical note and return coding output.
    """

    logger.info(f"Received request for note_id={note_id}")

    try:
        result = await engine.process_note(note_id)

        logger.info(f"Successfully processed note_id={note_id}")

        return result

    except ValueError as ve:
        logger.error(f"Validation error for note_id={note_id}: {ve}")
        raise HTTPException(status_code=404, detail=str(ve))

    except Exception as e:
        logger.exception(f"Unexpected error for note_id={note_id}")
        raise HTTPException(status_code=500, detail="Internal Server Error")