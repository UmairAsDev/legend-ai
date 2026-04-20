# database/pgdb/conn.py

import sys
import pathlib
import asyncio
sys.path.append(str(pathlib.Path(__file__).parent.parent.parent))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import URL, text
from sqlalchemy.exc import OperationalError
from contextlib import asynccontextmanager
from typing import AsyncIterator

from config.config import setting
from dotenv import load_dotenv
import logging
import ssl

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

file_handler = logging.FileHandler("logs/postgres.log")
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

load_dotenv()

# -------------------------
# SSL
# -------------------------
ssl_context = ssl.create_default_context()

# -------------------------
# Base
# -------------------------
Base = DeclarativeBase()

# -------------------------
# Database URL
# -------------------------
database_url = URL.create(
    "postgresql+asyncpg",
    username=setting.PGUSER,
    password=setting.PGPASSWORD,
    host=setting.PGHOST,
    database=setting.PGDATABASE,
    port=setting.PGPORT
)

# -------------------------
# Engine
# -------------------------
engine = create_async_engine(
    url=database_url,
    pool_pre_ping=True,
    connect_args={
        "ssl": ssl_context,
        "timeout": 5000,
        "command_timeout": 5000,
    },
)

# -------------------------
# Session Factory
# -------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# -------------------------
# Session Manager (FINAL FIX ✅)
# -------------------------
@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()   # ✅ CRITICAL FIX
        except Exception as e:
            await session.rollback()
            logger.error(f"Session rollback: {e}")
            raise
        finally:
            await session.close()

# -------------------------
# Test Connection
# -------------------------
async def test_connection():
    try:
        async with get_db_session() as db:
            result = await db.execute(text("SELECT 1"))
            logger.info(f"Connection OK: {result.scalar()}")

            # Optional debug (very useful)
            db_name = await db.execute(text("SELECT current_database()"))
            logger.info(f"Connected DB: {db_name.scalar()}")

    except TimeoutError:
        logger.error("Database connection timeout")
    except OperationalError as e:
        logger.error(f"Database error: {e}")

# -------------------------
# Run Directly
# -------------------------
if __name__ == "__main__":
    asyncio.run(test_connection())