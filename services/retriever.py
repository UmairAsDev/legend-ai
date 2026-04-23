import asyncio
from typing import List, Dict, Any
from sqlalchemy.engine import RowMapping
from decimal import Decimal

from database.pgdb.conn import get_db_session
from sqlalchemy import text
from loguru import logger


class CodeRetriever:

    # -------------------------
    # 🔹 Helper: pgvector format
    # -------------------------
    def _format_embedding(self, embedding: List[float]) -> str:
        return f"[{','.join(map(str, embedding))}]"

    # -------------------------
    # 🔹 CRITICAL: Clean DB row (Decimal fix)
    # -------------------------
    def _clean_row(self, row: RowMapping) -> Dict[str, Any]:
        cleaned = {}

        for k, v in row.items():   # RowMapping supports .items()
            if isinstance(v, Decimal):
                cleaned[k] = float(v)
            else:
                cleaned[k] = v

        return cleaned

    # -------------------------
    # 🔹 CPT VECTOR SEARCH
    # -------------------------
    async def _search_cpt(self, embedding: List[float], k: int) -> List[Dict[str, Any]]:
        emb = self._format_embedding(embedding)

        async with get_db_session() as db:
            result = await db.execute(
                text("""
                    SELECT 
                        proCode AS code,
                        codeDesc AS description,
                        proName,
                        associatedWithProCode,
                        minQty,
                        maxQty,
                        minSize,
                        chargePerUnit,
                        embedding <-> CAST(:embedding AS vector) AS distance,
                        'cpt' AS type
                    FROM cpt_embeddings
                    ORDER BY embedding <-> CAST(:embedding AS vector)
                    LIMIT :k
                """),
                {"embedding": emb, "k": k}
            )

            rows = [self._clean_row(row) for row in result.mappings().all()]
            return rows

    # -------------------------
    # 🔹 EM SEARCH
    # -------------------------
    async def _search_em(self, embedding: List[float], k: int) -> List[Dict[str, Any]]:
        emb = self._format_embedding(embedding)

        async with get_db_session() as db:
            result = await db.execute(
                text("""
                    SELECT 
                        enmCode AS code,
                        enmCodeDesc AS description,
                        encounterTime,
                        enmLevel,
                        embedding <-> CAST(:embedding AS vector) AS distance,
                        'em' AS type
                    FROM em_embeddings
                    ORDER BY embedding <-> CAST(:embedding AS vector)
                    LIMIT :k
                """),
                {"embedding": emb, "k": k}
            )

            return [self._clean_row(row) for row in result.mappings().all()]

    # -------------------------
    # 🔹 MODIFIER SEARCH
    # -------------------------
    async def _search_modifier(self, embedding: List[float], k: int) -> List[Dict[str, Any]]:
        emb = self._format_embedding(embedding)

        async with get_db_session() as db:
            result = await db.execute(
                text("""
                    SELECT 
                        modifier AS code,
                        modifierDesc AS description,
                        modifierDetDesc,
                        embedding <-> CAST(:embedding AS vector) AS distance,
                        'modifier' AS type
                    FROM modifier_embeddings
                    ORDER BY embedding <-> CAST(:embedding AS vector)
                    LIMIT :k
                """),
                {"embedding": emb, "k": k}
            )

            return [self._clean_row(row) for row in result.mappings().all()]

    # -------------------------
    # 🔹 MAIN SEARCH
    # -------------------------
    async def search(self, query_embedding: List[float], top_k: int = 40) -> List[Dict[str, Any]]:
        try:
            logger.info("🔍 Running multi-table vector search...")

            cpt_res, em_res, mod_res = await asyncio.gather(
                self._search_cpt(query_embedding, 30),
                self._search_em(query_embedding, 5),
                self._search_modifier(query_embedding, 5),
            )

            results: List[Dict[str, Any]] = []
            results.extend(cpt_res)
            results.extend(em_res)
            results.extend(mod_res)

            logger.info(f"✅ Retrieved {len(results)} candidates")

            return results

        except Exception as e:
            logger.exception(f"❌ Retrieval failed: {e}")
            raise

    # -------------------------
    # 🔴 BIOPSY FILTER
    # -------------------------
    async def biopsy_filter(self):
        async with get_db_session() as db:

            query = """
            SELECT 
                proCode AS code,
                codeDesc AS description,
                proName,
                associatedWithProCode,
                minQty,
                maxQty,
                chargePerUnit,
                0.0 AS distance,
                'cpt' AS type
            FROM cpt_embeddings
            WHERE 
                LOWER(codeDesc) LIKE '%biopsy%'
                OR LOWER(proName) LIKE '%biopsy%'
            """

            result = await db.execute(text(query))
            rows = [self._clean_row(row) for row in result.mappings().all()]

            logger.info(f"✅ Biopsy filter returned: {len(rows)} rows")

            return rows

    # -------------------------
    # 🔴 MOHS FILTER
    # -------------------------
    async def mohs_filter(self):
        async with get_db_session() as db:

            query = """
            SELECT 
                proCode AS code,
                codeDesc AS description,
                proName,
                associatedWithProCode,
                minQty,
                maxQty,
                chargePerUnit,
                0.0 AS distance,
                'cpt' AS type
            FROM cpt_embeddings
            WHERE 
                LOWER(codeDesc) LIKE '%mohs%'
                OR LOWER(proName) LIKE '%mohs%'
            """

            result = await db.execute(text(query))
            rows = [self._clean_row(row) for row in result.mappings().all()]

            logger.info(f"✅ Mohs filter returned: {len(rows)} rows")

            return rows
        

    async def excision_filter(self, size: float, location: str):

        async with get_db_session() as db:

            location = location.lower()

            # 🔹 Determine anatomical group
            if any(k in location for k in ["face", "nose", "lip", "ear", "eyelid"]):
                area = "face"
            elif any(k in location for k in ["hand", "foot", "neck", "scalp"]):
                area = "special"
            else:
                area = "trunk"

            logger.info(f"📍 Excision filter | size={size} | area={area}")

            query = """
            SELECT 
                proCode AS code,
                codeDesc AS description,
                proName,
                associatedWithProCode,
                minQty,
                maxQty,
                minSize,
                maxSize,
                chargePerUnit,
                0.0 AS distance,
                'cpt' AS type
            FROM cpt_embeddings
            WHERE LOWER(proName) LIKE '%excision%'
            """

            result = await db.execute(text(query))
            rows = [self._clean_row(r) for r in result.mappings().all()]

            # 🔴 FILTER BY SIZE
            filtered = []
            for r in rows:
                try:
                    min_s = float(r.get("minSize") or 0)
                    max_s = float(r.get("maxSize") or 999)

                    if size and min_s <= size <= max_s:
                        filtered.append(r)
                except:
                    continue

            logger.info(f"✅ Excision filtered: {len(filtered)}")

            return filtered