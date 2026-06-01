# database/sqldb/conn.py

import asyncio
import pathlib
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

sys.path.append(str(pathlib.Path(__file__).parent.parent.parent))

from loguru import logger
from sqlalchemy import MetaData, URL, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config.config import setting

Base = DeclarativeBase()

database_url = URL.create(
    "mysql+aiomysql",
    username=setting.DB_USERNAME,
    password=setting.DB_PASSWORD.get_secret_value(),
    database=setting.DB_NAME,
    host=setting.DB_HOST,
    port=int(setting.DB_PORT),
)

conn = create_async_engine(
    url=database_url,
    pool_pre_ping=True,
)

asyncSessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=conn,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
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
    logger.info("Testing MySQL connection...")
    try:
        async with get_db_session() as db:
            result = await db.execute(text("SELECT 1"))
            logger.info(f"MySQL connection OK: {result.scalar()}")
    except TimeoutError:
        logger.error("MySQL connection timed out.")
    except OperationalError as e:
        logger.error(f"MySQL connection failed: {e}")


async def _main():
    try:
        await test_connection()
    finally:
        await conn.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
