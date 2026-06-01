# database/pgdb/conn.py

import asyncio
import pathlib
import ssl
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

sys.path.append(str(pathlib.Path(__file__).parent.parent.parent))

from loguru import logger
from sqlalchemy import URL, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config.config import setting

Base = DeclarativeBase()

ssl_context = ssl.create_default_context()

database_url = URL.create(
    "postgresql+asyncpg",
    username=setting.PGUSER,
    password=setting.PGPASSWORD.get_secret_value(),
    host=setting.PGHOST,
    database=setting.PGDATABASE,
    port=setting.PGPORT,
)

engine = create_async_engine(
    url=database_url,
    pool_pre_ping=True,
    connect_args={
        "ssl": ssl_context,
        "timeout": 10,           # connection timeout in seconds
        "command_timeout": 60,   # per-query timeout in seconds
    },
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Session rollback: {e}")
            raise
        finally:
            await session.close()


async def test_connection():
    try:
        async with get_db_session() as db:
            result = await db.execute(text("SELECT 1"))
            logger.info(f"PostgreSQL connection OK: {result.scalar()}")
            db_name = await db.execute(text("SELECT current_database()"))
            logger.info(f"Connected database: {db_name.scalar()}")
    except TimeoutError:
        logger.error("PostgreSQL connection timeout")
    except OperationalError as e:
        logger.error(f"PostgreSQL connection error: {e}")


if __name__ == "__main__":
    asyncio.run(test_connection())
