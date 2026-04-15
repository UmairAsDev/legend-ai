import sys
import pathlib
sys.path.append(str(pathlib.Path(__file__).parent.parent))
from contextlib import contextmanager, asynccontextmanager
from database.sqldb.conn import asyncSessionLocal




@asynccontextmanager
async def async_db_session():
    session = asyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

