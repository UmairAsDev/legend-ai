import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
from services.embeddings import EmbeddingService

BASE_DIR = Path(__file__).resolve().parent.parent 

async def main():
    service = EmbeddingService()

    await service.ingest_csv(
        str(BASE_DIR / "data" / "proCodeList.csv"), "cpt"
    )
    await service.ingest_csv(
        str(BASE_DIR / "data" / "enmCodeList.csv"), "em"
    )
    await service.ingest_csv(
        str(BASE_DIR / "data" / "modifierList.csv"), "modifier"
    )

if __name__ == "__main__":
    asyncio.run(main())