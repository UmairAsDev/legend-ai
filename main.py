# main.py
"""
FastAPI application entry point with lifespan management.
"""
import sys
import uvicorn
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from database.sqldb.conn import conn
from app.route import router as medical_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage startup and shutdown events.
    """

    logger.info("🚀 Starting FastAPI application")

    try:
        yield

    finally:
        logger.info("🛑 Shutting down application")

        try:
            await conn.dispose()
            logger.info("✅ Database connection closed successfully")
        except Exception as e:
            logger.exception("❌ Error while closing DB connection")


app = FastAPI(
    title="Medical Coding API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(
    medical_router,
    prefix="/api/v1",
    tags=["Medical Coding"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    try:
        logger.info("Starting Uvicorn server...")

        uvicorn.run(
            "main:app", host="127.0.0.1", port=8002, reload=True, log_level="info",
        )

    except Exception:
        logger.exception("Failed to start server")