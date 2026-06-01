# main.py

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from loguru import logger
from sqlalchemy import text

sys.path.append(str(Path(__file__).parent))

from database.sqldb.conn import conn as mysql_engine, get_db_session as mysql_session
from database.pgdb.conn import get_db_session as pg_session
from app.api.route import router as medical_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Medical Coding API")
    try:
        yield
    finally:
        logger.info("Shutting down Medical Coding API")
        await mysql_engine.dispose()


app = FastAPI(
    title="Medical Coding API",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(
    medical_router,
    prefix="/api/v1",
    tags=["Medical Coding"],
)


@app.get("/health")
async def health():
    """Liveness + database connectivity check."""
    status: dict = {"status": "ok", "databases": {}}

    try:
        async with mysql_session() as db:
            await db.execute(text("SELECT 1"))
        status["databases"]["mysql"] = "ok"
    except Exception as e:
        logger.error(f"Health check: MySQL unreachable — {e}")
        status["databases"]["mysql"] = "unreachable"
        status["status"] = "degraded"

    try:
        async with pg_session() as db:
            await db.execute(text("SELECT 1"))
        status["databases"]["postgres"] = "ok"
    except Exception as e:
        logger.error(f"Health check: PostgreSQL unreachable — {e}")
        status["databases"]["postgres"] = "unreachable"
        status["status"] = "degraded"

    return status


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8002")),
        reload=os.getenv("APP_RELOAD", "false").lower() == "true",
    )
