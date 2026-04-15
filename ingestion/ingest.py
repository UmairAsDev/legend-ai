import sys
import logging
import pandas as pd
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from sqlalchemy import text
from database.pgdb.conn import get_db_session, conn


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion")

file_handler = logging.FileHandler("logs/ingestion.log")
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


file_path = Path(__file__).resolve().parent.parent / "data" / "icd10codes.csv"

df = pd.read_csv(file_path)
logger.info(f"Data prepared. Rows: {len(df)}")



async def ingest_data(df: pd.DataFrame, table_name: str):
    df = df[["code", "codedesc", "codedescext"]] #type:ignore

    df = df.where(pd.notnull(df), None)

    records = [
        (
            str(r[0]) if r[0] is not None else None,
            str(r[1]) if r[1] is not None else None,
            str(r[2]) if r[2] is not None else None,
        )
        for r in df.itertuples(index=False, name=None)
    ]
    logger.info(f"Starting COPY for {len(records)} records into {table_name}")
    async with conn.connect() as connection:  
        raw_conn = await connection.get_raw_connection()
        driver_conn = raw_conn.driver_connection   # asyncpg connection

        try:
            await driver_conn.copy_records_to_table(
                table_name,
                records=records,
                columns=["code", "codedesc", "codedescext"],
            )

            logger.info(f"COPY completed successfully: {len(records)} rows")

        except Exception as e:
            logger.error(f"COPY failed: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    import pathlib
    import asyncio

    file_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "icd10codes.csv"
    df = pd.read_csv(file_path)

    df = df.rename(columns={
        "CODE": "code",
        "CodeDesc": "codedesc",
        "CodeDescExtended": "codedescext"
    })

    asyncio.run(ingest_data(df, "icd10_codes"))
