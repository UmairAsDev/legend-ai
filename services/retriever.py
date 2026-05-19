import asyncio
from typing import List, Dict, Any
from sqlalchemy.engine import RowMapping
from decimal import Decimal

import re
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
    def _clean_row(self, row):
        cleaned = {}

        for k, v in row.items():

            # -------------------------
            # 🔴 NORMALIZE associatedWithProCode (CRITICAL FIX)
            # -------------------------
            if k == "associatedWithProCode":
                if v is None:
                    cleaned[k] = None
                else:
                    val = str(v).strip()

                    # 🔴 remove .0 again (safety layer)
                    if val.endswith(".0"):
                        val = val[:-2]

                    if val in ["", "0", "None", "null"]:
                        cleaned[k] = None
                    else:
                        cleaned[k] = val

            # -------------------------
            # 🔴 NORMALIZE code
            # -------------------------
            elif k == "code":
                cleaned[k] = str(v).strip() if v is not None else None

            # -------------------------
            # 🔴 NUMERIC FIX
            # -------------------------
            elif isinstance(v, Decimal):
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
                        maxSize,
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
    # 🔹 DESTRUCTION QURIES
    # -------------------------
    async def _destruction_benign_filter(self, quantity: int):
        async with get_db_session() as db:

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
            WHERE LOWER(proName) = 'destruction benign'
            """

            result = await db.execute(text(query))

            rows = [
                self._clean_row(r)
                for r in result.mappings().all()
            ]

            filtered = []

            for r in rows:
                min_q = r.get("minQty") or 0
                max_q = r.get("maxQty") or 999

                if min_q <= quantity <= max_q:
                    filtered.append(r)

            logger.info(
                f"✅ Destruction benign candidates: {len(filtered)}"
            )

            return filtered
        

    async def _destruction_premalignant_filter(self, quantity: int):
        async with get_db_session() as db:

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
            WHERE LOWER(proName) = 'destruction premalignant lesion'
            """

            result = await db.execute(text(query))

            rows = [
                self._clean_row(r)
                for r in result.mappings().all()
            ]

            logger.info(f"📦 Raw DPM candidates: {len(rows)}")

            base_codes = []
            addon_codes = []

            for r in rows:

                parent = r.get("associatedWithProCode")

                if parent:
                    addon_codes.append(r)
                else:
                    base_codes.append(r)

            filtered = []

            # -------------------------
            # 🔴 PRIMARY
            # -------------------------
            for r in base_codes:
                min_q = r.get("minQty") or 0
                max_q = r.get("maxQty") or 999

                if min_q <= quantity <= max_q:
                    filtered.append(r)

            # -------------------------
            # 🔴 ADD-ON
            # -------------------------
            if quantity > 14:
                filtered.extend(addon_codes)

            logger.info(
                f"✅ DPM filtered candidates: {len(filtered)}"
            )

            return filtered
        

    async def _destruction_malignant_filter(
        self,
        quantity: int,
        size: float | None,
        location: str | None = None
    ):

        async with get_db_session() as db:

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
            WHERE LOWER(proName) = 'destruction malignant lesion'
            """

            result = await db.execute(text(query))

            rows = [
                self._clean_row(r)
                for r in result.mappings().all()
            ]

            logger.info(
                f"📦 Raw DM candidates: {len(rows)}"
            )

            location = (location or "").lower()

            # -------------------------------------------------
            # 🔴 DETERMINE ANATOMICAL GROUP
            # -------------------------------------------------
            face_keywords = [
                "face", "cheek", "nose", "lip",
                "eyelid", "ear", "forehead",
                "temple", "chin", "mucous"
            ]

            special_keywords = [
                "scalp", "neck", "hand",
                "foot", "feet", "genital"
            ]

            anatomical_group = "trunk"

            if any(k in location for k in face_keywords):
                anatomical_group = "face"

            elif any(k in location for k in special_keywords):
                anatomical_group = "special"

            logger.info(
                f"🧠 DM anatomical group="
                f"{anatomical_group}"
            )

            filtered = []

            for r in rows:

                desc = (
                    r.get("description", "")
                    or ""
                ).lower()

                # -------------------------------------------------
                # 🔴 ANATOMICAL FILTER
                # -------------------------------------------------
                if anatomical_group == "face":

                    if (
                        "face" not in desc
                        and "ears" not in desc
                        and "eyelids" not in desc
                        and "nose" not in desc
                        and "lips" not in desc
                        and "mucous membrane" not in desc
                    ):
                        continue

                elif anatomical_group == "special":

                    if (
                        "scalp" not in desc
                        and "neck" not in desc
                        and "hands" not in desc
                        and "feet" not in desc
                        and "genitalia" not in desc
                    ):
                        continue

                else:

                    if (
                        "trunk" not in desc
                        and "arms" not in desc
                        and "legs" not in desc
                    ):
                        continue

                # -------------------------------------------------
                # 🔴 SIZE FILTER
                # -------------------------------------------------
                try:

                    min_s = float(r.get("minSize") or 0)
                    max_s = float(r.get("maxSize") or 999)

                    if size is None:
                        continue

                    # EXACT RANGE MATCH
                    if min_s <= size <= max_s:

                        logger.info(
                            f"✅ DM MATCH | "
                            f"code={r['code']} | "
                            f"size={size} | "
                            f"range={min_s}-{max_s}"
                        )

                        filtered.append(r)

                except Exception as e:

                    logger.warning(
                        f"⚠️ DM filter failed "
                        f"for code={r.get('code')} | {e}"
                    )

                    continue

            logger.info(
                f"🎯 FINAL DM candidates: "
                f"{len(filtered)}"
            )

            logger.info(
                f"📦 DM FINAL CODES: "
                f"{[r['code'] for r in filtered]}"
            )

            return filtered

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
    # 🔴 MOHS FILTER (FINAL FIXED)
    # -------------------------
    async def mohs_filter(self, location: str):
        async with get_db_session() as db:

            location = (location or "").lower()

            logger.info(f"📍 Mohs filter | location={location}")

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

            logger.info(f"📦 Raw Mohs candidates: {len(rows)}")

            filtered = []

            # -------------------------
            # 🔴 LOCATION FILTER
            # -------------------------
            high_risk = [
                "head", "neck", "temple", "face", "jaw",
                "scalp", "ear", "eyelid", "nose", "lip",
                "hand", "foot", "genitalia", "auricle"
            ]

            is_high_risk = any(k in location for k in high_risk)

            logger.info(f"🧠 Mohs classification → high_risk={is_high_risk}")

            for r in rows:
                code = str(r.get("code", ""))

                # High-risk → 17311/17312
                if is_high_risk and code in ["17311", "17312"]:
                    filtered.append(r)

                # Trunk → 17313/17314
                elif not is_high_risk and code in ["17313", "17314"]:
                    filtered.append(r)

            logger.info(f"🎯 Mohs filtered candidates: {len(filtered)}")

            # -------------------------
            # 🔴 SAFETY: missing location
            # -------------------------
            if not location:
                logger.warning("⚠️ Missing Mohs location → returning ALL Mohs codes (no filtering)")
                return rows

            # -------------------------
            # 🔴 SAFETY: no match after filtering
            # -------------------------
            if not filtered:
                logger.warning("⚠️ No filtered Mohs match → returning ALL Mohs codes")
                return rows

            # -------------------------
            # ✅ FINAL RETURN (CRITICAL FIX)
            # -------------------------
            logger.info(f"✅ Returning {len(filtered)} filtered Mohs candidates")
            return filtered
        


    async def excision_filter(self, size: float, location: str):
        async with get_db_session() as db:

            location = (location or "").lower()

            # -------------------------
            # 🔴 TOKENIZE (word-safe)
            # -------------------------
            tokens = set(re.findall(r"\b[a-z]+\b", location))

            # -------------------------
            # 🔴 AREA CLASSIFICATION (DETERMINISTIC)
            # -------------------------
            FACE = {"face", "nose", "lip", "ear", "eyelid", "mucous membrane"}
            SPECIAL = {"hand", "foot", "feet", "neck", "scalp", "finger", "toe"}

            if tokens & FACE:
                area = "face"
            elif tokens & SPECIAL:
                area = "special"
            else:
                area = "trunk"

            logger.info(f"📍 Excision filter | size={size} | location={location} | area={area}")

            # -------------------------
            # 🔴 BASE QUERY
            # -------------------------
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
            WHERE 
                LOWER(proName) LIKE '%excision%'
                AND LOWER(codeDesc) NOT LIKE '%closure%'
            """

            result = await db.execute(text(query))
            rows = [self._clean_row(r) for r in result.mappings().all()]

            logger.info(f"📦 Raw excision candidates: {len(rows)}")

            filtered = []

            # -------------------------
            # 🔴 STRICT FILTERING
            # -------------------------
            for r in rows:
                try:
                    code = str(r.get("code", ""))
                    desc = (r.get("description") or "").lower()
                    pro_name = (r.get("proName") or "").lower()

                    # -------------------------
                    # 1. KEEP ONLY SKIN EXCISION (114xx, 116xx)
                    # -------------------------
                    if not (code.startswith("114") or code.startswith("116")):
                        continue

                    # -------------------------
                    # 2. REMOVE IRRELEVANT TYPES
                    # -------------------------
                    if any(x in pro_name for x in [
                        "soft tissue",
                        "nail",
                        "matrix",
                        "chalazion",
                        "non skin"
                    ]):
                        continue

                    # -------------------------
                    # 3. SIZE FILTER (STRICT)
                    # -------------------------
                    min_s = float(r.get("minSize") or 0)
                    max_s = float(r.get("maxSize") or 999)

                    if not size or not (min_s <= size <= max_s):
                        continue

                    # -------------------------
                    # 4. LOCATION FILTER (STRICT)
                    # -------------------------
                    if area == "face":
                        if not any(k in desc for k in ["face", "ear", "eyelid", "nose", "lip", "mucous membrane"]):
                            continue

                    elif area == "special":
                        if not any(k in desc for k in ["scalp", "neck", "hand", "foot", "feet", "genitalia"]):
                            continue

                    elif area == "trunk":
                        if not any(k in desc for k in ["trunk", "arm", "forearm", "leg", "foreleg", "forelimb", "back", "chest"]):
                            continue

                    filtered.append(r)

                except Exception as e:
                    logger.warning(f"⚠️ Filter skip: {e}")
                    continue

            logger.info(f"🎯 Candidates after strict filtering: {len(filtered)}")

            # -------------------------
            # 🔴 SAFETY FALLBACK (if too strict)
            # -------------------------
            if not filtered:
                logger.warning("⚠️ No strict matches → relaxing location constraint")

                for r in rows:
                    try:
                        code = str(r.get("code", ""))

                        if not (code.startswith("114") or code.startswith("116")):
                            continue

                        min_s = float(r.get("minSize") or 0)
                        max_s = float(r.get("maxSize") or 999)

                        if size and (min_s <= size <= max_s):
                            filtered.append(r)

                    except:
                        continue

                logger.info(f"🔁 Fallback candidates: {len(filtered)}")

            return filtered
        

    # -------------------------
    # 🔴 CLOSURE FILTER
    # -------------------------
    async def closure_filter(self, size: float, location: str, ctype: str):
        async with get_db_session() as db:

            location = (location or "").lower()
            ctype = (ctype or "").lower()

            logger.info(
                f"🔍 Closure filter | type={ctype}, size={size}, location_group={location}"
            )

            # =========================================================
            # 🔴 LOAD ALL CLOSURE CODES (120xx + 131xx)
            # =========================================================
            query = """
            SELECT 
                proCode AS code,
                codeDesc AS description,
                proName,
                associatedWithProCode,
                minSize,
                maxSize,
                chargePerUnit,
                0.0 AS distance,
                'cpt' AS type
            FROM cpt_embeddings
            WHERE 
                proCode LIKE '120%%' OR proCode LIKE '131%%' OR proCode LIKE '140%%'
            """

            result = await db.execute(text(query))
            rows = [self._clean_row(row) for row in result.mappings().all()]

            logger.info(f"📦 Raw closure candidates: {len(rows)}")

            filtered = []

            # =========================================================
            # 🔴 FILTER PIPELINE: TYPE → LOCATION → SIZE
            # =========================================================
            for r in rows:
                try:
                    code = str(r.get("code") or "")
                    desc = (r.get("description") or "").lower()

                    # -------------------------
                    # 🔴 TYPE FILTER
                    # -------------------------
                    if ctype == "complex":
                        if not code.startswith("131"):
                            continue

                    elif ctype == "adjacent":
                        if not code.startswith("140"):
                            continue

                    elif ctype == "intermediate":
                        if not code.startswith("120"):
                            continue

                    else:
                        logger.warning(f"⚠️ Unknown closure type: {ctype}")
                        continue

                    # -------------------------
                    # 🔴 LOCATION FILTER (CRITICAL FIX)
                    # -------------------------
                    if location:

                        # Extremities → scalp, arm, leg
                        if location == "extremities":
                            if not any(k in desc for k in ["scalp", "arm", "leg"]):
                                continue

                        # Critical → eyelids, nose, lips, ears
                        elif location == "critical":
                            if not any(k in desc for k in ["eyelid", "nose", "lip", "ear"]):
                                continue

                        # High-risk → face, hands, feet, genitalia
                        elif location == "high_risk":
                            if not any(k in desc for k in [
                                "axillae", "face", "hand", "foot", "feet", "genitalia", "neck", "chin", "cheek", "forehead", "mouth"
                            ]):
                                continue

                        # Trunk → chest, back, abdomen
                        elif location == "trunk":
                            if not any(k in desc for k in ["trunk", "back", "chest", "abdomen"]):
                                continue

                    # -------------------------
                    # 🔴 SIZE FILTER (STRICT)
                    # -------------------------
                    min_size = float(r.get("minSize") or 0)
                    max_size = float(r.get("maxSize") or 999)

                    if size and (min_size <= size <= max_size):
                        filtered.append(r)

                except Exception as e:
                    logger.warning(f"⚠️ Closure filter skip: {e}")
                    continue

            logger.info(f"🎯 Closure filtered (strict): {len(filtered)}")

            # =========================================================
            # 🔴 FALLBACK 1: TYPE + SIZE ONLY
            # =========================================================
            if not filtered:
                logger.warning("⚠️ No strict match → fallback (type + size)")

                for r in rows:
                    try:
                        code = str(r.get("code") or "")

                        if ctype == "complex" and not code.startswith("131"):
                            continue
                        if ctype == "intermediate" and not code.startswith("120"):
                            continue

                        min_size = float(r.get("minSize") or 0)
                        max_size = float(r.get("maxSize") or 999)

                        if size and (min_size <= size <= max_size):
                            filtered.append(r)

                    except:
                        continue

                logger.info(f"🔁 Fallback (type+size) candidates: {len(filtered)}")

            # =========================================================
            # 🔴 FALLBACK 2: SIZE ONLY
            # =========================================================
            if not filtered:
                logger.warning("⚠️ No match → fallback (size only)")

                for r in rows:
                    try:
                        min_size = float(r.get("minSize") or 0)
                        max_size = float(r.get("maxSize") or 999)

                        if size and (min_size <= size <= max_size):
                            filtered.append(r)

                    except:
                        continue

                logger.info(f"🔁 Fallback (size only): {len(filtered)}")

            # =========================================================
            # 🔴 FINAL SAFETY
            # =========================================================
            if not filtered:
                logger.error("❌ Closure filter empty → returning ALL closure codes")
                return rows

            # =========================================================
            # 🔴 DEBUG OUTPUT (VERY IMPORTANT)
            # =========================================================
            for r in filtered:
                logger.info(
                    f"✅ Candidate → code={r['code']} | desc={r['description']} | "
                    f"size_range=({r.get('minSize')},{r.get('maxSize')}) | parent={r.get('associatedWithProCode')}"
                )

            return filtered
        

    # -------------------------
    # 🔴 SRT FILTER
    # -------------------------
    async def srt_filter(self, srt_section):
        async with get_db_session() as db:

            kv = srt_section.get("kv")
            ultrasound = srt_section.get("ultrasound")
            images_present = srt_section.get("images_present")

            logger.info(
                f"🎯 SRT filter → kv={kv}, ultrasound={ultrasound}, images={images_present}"
            )

            query = """
            SELECT 
                proCode AS code,
                codeDesc AS description,
                proName,
                associatedWithProCode,
                0.0 AS distance,
                'cpt' AS type
            FROM cpt_embeddings
            WHERE proCode IN ('77436','77437','77438','77439')
            """

            result = await db.execute(text(query))
            rows = [self._clean_row(r) for r in result.mappings().all()]

            selected = []

            # 🔴 ALWAYS ADD 77436
            selected.extend([r for r in rows if r["code"] == "77436"])

            # 🔴 DELIVERY
            if kv and kv <= 150:
                selected.extend([r for r in rows if r["code"] == "77437"])
            elif kv and kv > 150:
                selected.extend([r for r in rows if r["code"] == "77438"])

            # 🔴 ADD-ON (STRICT)
            if ultrasound and images_present:
                logger.info("✅ 77439 allowed")
                selected.extend([r for r in rows if r["code"] == "77439"])
            else:
                logger.info("🚫 77439 blocked")

            logger.info(f"✅ Final SRT codes: {[r['code'] for r in selected]}")

            return selected
        
    # -------------------------
    # 🔴 DEBRIDEMENT FILTER
    # -------------------------
    async def debridement_filter(self, section):
        async with get_db_session() as db:

            depth = section.get("depth")
            nail = section.get("nail")
            dermatologic = section.get("dermatologic")
            is_wound = section.get("is_wound")
            quantity = section.get("quantity")

            logger.info(
                f"🎯 Debridement filter → depth={depth}, nail={nail}, "
                f"derm={dermatologic}, wound={is_wound}, qty={quantity}"
            )

            query = """
            SELECT 
                proCode AS code,
                codeDesc AS description,
                proName,
                associatedWithProCode,
                0.0 AS distance,
                'cpt' AS type
            FROM cpt_embeddings
            WHERE proCode IN ('11040','11041','11042','11720','11721','11000')
            """

            result = await db.execute(text(query))
            rows = [self._clean_row(r) for r in result.mappings().all()]

            selected = []

            # -------------------------
            # 🔴 NAIL
            # -------------------------
            if nail:
                if quantity <= 5:
                    selected.extend([r for r in rows if r["code"] == "11720"])
                else:
                    selected.extend([r for r in rows if r["code"] == "11721"])

                logger.info(f"✅ Nail codes: {[r['code'] for r in selected]}")
                return selected

            # -------------------------
            # 🔴 DERMATOLOGIC (11000)
            # -------------------------
            if dermatologic and not is_wound:
                logger.info("✅ Dermatologic debridement → 11000")
                selected.extend([r for r in rows if r["code"] == "11000"])
                return selected

            # -------------------------
            # 🔴 WOUND DEPTH
            # -------------------------
            if depth == "partial":
                selected.extend([r for r in rows if r["code"] == "11040"])

            elif depth == "full":
                selected.extend([r for r in rows if r["code"] == "11041"])

            elif depth == "subcutaneous":
                selected.extend([r for r in rows if r["code"] == "11042"])

            else:
                logger.warning("⚠️ Unknown depth → fallback 11040")
                selected.extend([r for r in rows if r["code"] == "11040"])

            logger.info(f"✅ Debridement codes: {[r['code'] for r in selected]}")

            return selected
        

    # -------------------------
    # 🔴 DESTRUCTION FILTER
    # -------------------------
    async def destruction_filter(
        self,
        destruction_type: str,
        quantity: int,
        size: float | None = None,
        location: str | None = None
    ):

        if destruction_type == "db":
            return await self._destruction_benign_filter(quantity)

        elif destruction_type == "dpm":
            return await self._destruction_premalignant_filter(quantity)

        elif destruction_type == "dm":
            return await self._destruction_malignant_filter(quantity, size, location)

        return []
    

    # =========================================================
    # 🔹 SHAVE REMOVAL FILTER
    # =========================================================
    async def shave_removal_filter(
        self,
        location_group: str,
        size: float | None
    ):

        async with get_db_session() as db:

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
            WHERE LOWER(proName) = 'shave removal'
            """

            result = await db.execute(text(query))

            rows = [
                self._clean_row(r)
                for r in result.mappings().all()
            ]

            filtered = []

            for r in rows:

                try:

                    desc = (
                        r.get("description") or ""
                    ).lower()

                    # -------------------------
                    # LOCATION FILTER
                    # -------------------------
                    if location_group == "face":

                        if not any(k in desc for k in [
                            "face", "ears", "eyelids",
                            "nose", "lips", "mucous membrane"
                        ]):
                            continue

                    elif location_group == "special":

                        if not any(k in desc for k in [
                            "scalp", "neck",
                            "hands", "feet",
                            "genitalia"
                        ]):
                            continue

                    else:

                        if not any(k in desc for k in [
                            "trunk", "arms", "legs"
                        ]):
                            continue

                    # -------------------------
                    # SIZE FILTER
                    # -------------------------
                    if size is not None:

                        min_s = float(r.get("minSize") or 0)
                        max_s = float(r.get("maxSize") or 999)

                        if not (min_s <= size <= max_s):
                            continue

                    filtered.append(r)

                except Exception as e:
                    logger.warning(
                        f"⚠️ Shave filter failed: {e}"
                    )

            # -------------------------
            # FALLBACK
            # if no size → choose smallest code
            # -------------------------
            if size is None and filtered:

                filtered = sorted(
                    filtered,
                    key=lambda x: float(x.get("maxSize") or 0)
                )

                return [filtered[0]]

            logger.info(
                f"✅ Shave candidates: {len(filtered)}"
            )

            return filtered
        

    # =========================================================
    # 🔹 LASER TREATMENT FILTER
    # =========================================================
    async def laser_treatment_filter(
        self,
        section,
        full_procedure_text: str
    ):

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
            WHERE LOWER(proName) = 'laser treatment'
            """

            result = await db.execute(text(query))

            rows = [
                self._clean_row(r)
                for r in result.mappings().all()
            ]

            logger.info(
                f"📦 Raw laser candidates: {len(rows)}"
            )

            method = (
                section.get("method") or ""
            ).lower()

            procedure_text = (
                full_procedure_text or ""
            ).lower()

            # -------------------------------------------------
            # 🔴 STEP 1
            # METHOD MATCH
            # -------------------------------------------------
            if method:

                normalized_method = re.sub(
                    r"(laser|treatment|therapy)",
                    "",
                    method
                ).strip()

                logger.info(
                    f"🔍 Laser method normalized="
                    f"{normalized_method}"
                )

                matched = []

                for r in rows:

                    desc = (
                        r.get("description") or ""
                    ).lower()

                    desc_clean = re.sub(
                        r"(laser|treatment)",
                        "",
                        desc
                    ).strip()

                    if normalized_method in desc_clean:

                        logger.info(
                            f"✅ METHOD MATCH → "
                            f"{r['code']}"
                        )

                        matched.append(r)

                if matched:
                    return matched

            # -------------------------------------------------
            # 🔴 STEP 2
            # KEYWORD MATCH FROM PROCEDURE TEXT
            # -------------------------------------------------
            matched = []

            for r in rows:

                desc = (
                    r.get("description") or ""
                ).lower()

                desc_clean = re.sub(
                    r"(laser|treatment)",
                    "",
                    desc
                ).strip()

                keywords = [
                    k.strip()
                    for k in desc_clean.split()
                    if len(k.strip()) > 3
                ]

                if any(k in procedure_text for k in keywords):

                    logger.info(
                        f"✅ PROCEDURE KEYWORD MATCH → "
                        f"{r['code']}"
                    )

                    matched.append(r)

            if matched:
                return matched

            # -------------------------------------------------
            # 🔴 STEP 3
            # DEFAULT CL001
            # -------------------------------------------------
            fallback = [
                r for r in rows
                if r.get("code") == "CL001"
            ]

            logger.info(
                "⚠️ Laser fallback → CL001"
            )

            return fallback
        

    # =========================================================
    # 🔹 XTRAC FILTER
    # =========================================================
    async def xtrac_filter(
        self,
        total_area: float | None
    ):

        async with get_db_session() as db:

            query = """
            SELECT
                proCode AS code,
                codeDesc AS description,
                proName,
                associatedWithProCode,
                minQty,
                maxQty,
                CAST(minsize AS FLOAT) AS "minSize",
                CAST(maxsize AS FLOAT) AS "maxSize",
                chargePerUnit,
                0.0 AS distance,
                'cpt' AS type
            FROM cpt_embeddings
            WHERE LOWER(proName) = 'xtrac laser treatment'
            """

            result = await db.execute(text(query))

            rows = [
                self._clean_row(r)
                for r in result.mappings().all()
            ]

            logger.info(
                f"📦 Raw Xtrac candidates: {len(rows)}"
            )

            # -------------------------------------------------
            # 🔴 FALLBACK
            # -------------------------------------------------
            if total_area is None:

                logger.warning(
                    "⚠️ Missing Xtrac total area "
                    "→ fallback 96920"
                )

                fallback = [
                    r for r in rows
                    if r.get("code") == "96920"
                ]

                return fallback

            # -------------------------------------------------
            # 🔴 RANGE FILTER
            # -------------------------------------------------
            filtered = []

            for r in rows:

                try:

                    min_s = float(r.get("minSize") or 0)
                    max_s = float(r.get("maxSize") or 999999)

                    if min_s <= total_area <= max_s:

                        logger.info(
                            f"✅ Xtrac match → "
                            f"{r['code']} | "
                            f"area={total_area} | "
                            f"range={min_s}-{max_s}"
                        )

                        filtered.append(r)

                except Exception as e:

                    logger.warning(
                        f"⚠️ Xtrac filter failed "
                        f"for code={r.get('code')} | {e}"
                    )

            # -------------------------------------------------
            # 🔴 SAFETY FALLBACK
            # -------------------------------------------------
            if not filtered:

                logger.warning(
                    "⚠️ No Xtrac range match "
                    "→ fallback 96920"
                )

                filtered = [
                    r for r in rows
                    if r.get("code") == "96920"
                ]

            logger.info(
                f"🎯 FINAL Xtrac candidates: "
                f"{[r['code'] for r in filtered]}"
            )

            return filtered