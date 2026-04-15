import sys
import pathlib
import asyncio
sys.path.append(str(pathlib.Path(__file__).parent.parent.parent))
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData, URL
from config.config import setting
from loguru import logger
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import AsyncIterator
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
import logging
import os

file_path = pathlib.Path(__file__).resolve() / "logs"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
file_handler = logging.FileHandler("logs/postgres.log")
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
load_dotenv()


import ssl

ssl_context = ssl.create_default_context()


Base = DeclarativeBase()

database_url = URL.create(
    "postgresql+asyncpg",
    username=setting.PGUSER,
    password=setting.PGPASSWORD, 
    host=setting.PGHOST,
    database=setting.PGDATABASE,
    port=setting.PGPORT
)



conn = create_async_engine(
    url=database_url,
    pool_pre_ping=True,
    connect_args={
        "ssl": ssl_context,
        "timeout": 5000,
        "command_timeout": 5000,
    },
)

asyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=conn)


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provides a transactional scope around a series of operations."""
    asyncsession = asyncSessionLocal()
    try:
        yield asyncsession
        await asyncsession.commit()
    except Exception as e:
        await asyncsession.rollback()
        logger.error(f"Session rollback due to error: {e}")
        raise
    finally:
        await asyncsession.close()



async def test_connection():
    logger.info("PostgreSQL connection module executed directly.")
    try:
        async with get_db_session() as db:
            result = await db.execute(text("SELECT 1"))
            logger.info(f"Connection Successful: {result.scalar()}")
    except TimeoutError:
        logger.error(
            "Database connection timed out. Check network access, PGHOST/PGPORT, and SSL settings."
        )
    except OperationalError as e:
        logger.error(f"Database connection failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_connection())