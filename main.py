# main.py

import sys
import uvicorn
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from database.sqldb.conn import conn
from app.api.route import router as medical_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting application")
    try:
        yield
    finally:
        logger.info("🛑 Shutting down")
        await conn.dispose()


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
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8002,
        reload=True
    )