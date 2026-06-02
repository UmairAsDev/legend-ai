# services/retriever.py
"""
Database fallback retriever for CPT code candidates.

Role in the pipeline
--------------------
Each deterministic selector is tried first.  When a selector cannot
determine a code (missing data, novel procedure variant) this retriever
queries the cpt_embeddings database table and returns "candidate"
confidence codes for the LLM to choose from.

Design rules
------------
- No CPT code numbers hardcoded in SQL WHERE clauses.
  All queries filter by proName — the same column the selectors use.
- All location filtering uses match_desc_by_location() from base.py,
  ensuring identical keyword sets throughout the pipeline.
- A single _fetch_by_proname() helper carries the standard SELECT
  so every filter method composes, not duplicates.
"""

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text

from config.constants import SRT_KV_BOUNDARY, MIN_METHOD_TOKEN_LENGTH
from database.pgdb.conn import get_db_session
from services.code_selectors.base import (
    classify_location,
    classify_mohs_risk,
    match_desc_by_location,
)

_UNKNOWN_MAX_SIZE = 9999.0


# ─────────────────────────────────────────────────────────────────────────────
# STANDARD SQL — shared column list for all filter queries
# ─────────────────────────────────────────────────────────────────────────────

_SELECT = """
    SELECT
        proCode                         AS code,
        codeDesc                        AS description,
        proName,
        associatedWithProCode,
        minQty,
        maxQty,
        CAST(minsize AS FLOAT)          AS "minSize",
        CAST(maxsize AS FLOAT)          AS "maxSize",
        chargePerUnit,
        0.0                             AS distance,
        'cpt'                           AS type
    FROM cpt_embeddings
"""


# ─────────────────────────────────────────────────────────────────────────────
# ROW CLEANING
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_assoc(val) -> Optional[str]:
    """Normalise associatedWithProCode — single definition shared with base.py logic."""
    if not val:
        return None
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s not in ("", "0", "None", "null") else None


def _clean_row(row: Any) -> Dict:
    """Normalise a database row to a plain dict with consistent field types."""
    cleaned: Dict = {}
    for k, v in row.items():
        if k == "associatedWithProCode":
            cleaned[k] = _normalise_assoc(v)
        elif k == "code":
            cleaned[k] = str(v).strip() if v is not None else None
        elif isinstance(v, Decimal):
            cleaned[k] = float(v)
        else:
            cleaned[k] = v
    return cleaned


def _size_match(row: Dict, size: float, epsilon: float = 0.005) -> bool:
    """True when size falls within the code's [minSize, maxSize] range."""
    min_s = float(row.get("minSize") or 0)
    max_s = float(row.get("maxSize") or _UNKNOWN_MAX_SIZE)
    return min_s <= size <= max_s + epsilon


def _qty_match(row: Dict, quantity: int) -> bool:
    """True when quantity falls within the code's [minQty, maxQty] range."""
    min_q = int(row.get("minQty") or 0)
    max_q = int(row.get("maxQty") or _UNKNOWN_MAX_SIZE)
    return min_q <= quantity <= max_q


# ─────────────────────────────────────────────────────────────────────────────
# CODE RETRIEVER
# ─────────────────────────────────────────────────────────────────────────────

class CodeRetriever:
    """
    Database fallback for CPT candidates when deterministic selectors
    cannot determine a code.

    All public filter methods are async and return List[dict] in the same
    format as the selector make_code() output.
    """

    # ── Core SQL helper ──────────────────────────────────────────────────────

    async def _fetch_by_proname(self, pro_name: str) -> List[Dict]:
        """Fetch all codes for a proName from the cpt_embeddings table."""
        async with get_db_session() as db:
            result = await db.execute(
                text(_SELECT + "WHERE LOWER(proName) = :name"),
                {"name": pro_name.lower()},
            )
            rows = [_clean_row(r) for r in result.mappings().all()]
            logger.debug(f"DB fetch [{pro_name!r}]: {len(rows)} rows")
            return rows

    async def _fetch_by_pronames(self, pro_names: List[str]) -> List[Dict]:
        """Fetch codes for multiple proNames in a single query."""
        if not pro_names:
            return []
        async with get_db_session() as db:
            placeholders = ",".join(f":n{i}" for i in range(len(pro_names)))
            params = {f"n{i}": name.lower() for i, name in enumerate(pro_names)}
            result = await db.execute(
                text(_SELECT + f"WHERE LOWER(proName) IN ({placeholders})"),
                params,
            )
            rows = [_clean_row(r) for r in result.mappings().all()]
            logger.debug(f"DB fetch {pro_names}: {len(rows)} rows")
            return rows

    # ── Destruction ──────────────────────────────────────────────────────────

    async def destruction_filter(
        self,
        destruction_type: str,
        quantity: int,
        size: Optional[float] = None,
        location: Optional[str] = None,
    ) -> List[Dict]:
        dtype = (destruction_type or "").lower()
        if dtype in ("db", "dbm"):
            return await self._destruction_benign_filter(quantity)
        if dtype == "dpm":
            return await self._destruction_premalignant_filter(quantity)
        if dtype == "dm":
            return await self._destruction_malignant_filter(quantity, size, location)
        return []

    async def _destruction_benign_filter(self, quantity: int) -> List[Dict]:
        rows = await self._fetch_by_proname("Destruction Benign")
        filtered = [r for r in rows if _qty_match(r, quantity)]
        logger.info(f"Destruction benign candidates: {len(filtered)}")
        return filtered

    async def _destruction_premalignant_filter(self, quantity: int) -> List[Dict]:
        rows = await self._fetch_by_proname("Destruction Premalignant Lesion")
        base_codes  = [r for r in rows if not r.get("associatedWithProCode")]
        addon_codes = [r for r in rows if r.get("associatedWithProCode")]

        filtered = [r for r in base_codes if _qty_match(r, quantity)]

        primary_max = max((r.get("maxQty") or 0 for r in filtered), default=0)
        if filtered and quantity > primary_max:
            filtered.extend(addon_codes)

        logger.info(f"DPM candidates: {len(filtered)}")
        return filtered

    async def _destruction_malignant_filter(
        self,
        quantity: int,
        size: Optional[float],
        location: Optional[str],
    ) -> List[Dict]:
        rows = await self._fetch_by_proname("Destruction Malignant Lesion")
        loc_group = classify_location(location or "")
        pool = match_desc_by_location(rows, loc_group)

        if size is None:
            logger.info("DM: no size — returning location-filtered candidates")
            return pool

        filtered = [r for r in pool if _size_match(r, size)]
        logger.info(f"DM candidates: {len(filtered)}")
        return filtered

    # ── Biopsy ───────────────────────────────────────────────────────────────

    async def biopsy_filter(self) -> List[Dict]:
        rows = await self._fetch_by_proname("Biopsy")
        logger.info(f"Biopsy filter: {len(rows)} rows")
        return rows

    # ── Excision ─────────────────────────────────────────────────────────────

    async def excision_filter(self, size: float, location: str) -> List[Dict]:
        rows = await self._fetch_by_pronames([
            "Excision Benign Lesion & Margins",
            "Excision Malignant Lesion & Margins",
        ])
        loc_group = classify_location(location or "")
        pool = match_desc_by_location(rows, loc_group)
        filtered = [r for r in pool if _size_match(r, size)]

        if not filtered:
            logger.warning("Excision: no strict match — relaxing location constraint")
            filtered = [r for r in rows if _size_match(r, size)]

        logger.info(f"Excision candidates: {len(filtered)}")
        return filtered

    # ── Mohs ─────────────────────────────────────────────────────────────────

    async def mohs_filter(self, location: str) -> List[Dict]:
        rows = await self._fetch_by_pronames([
            "MOHS Micrographic Surgery",
            "MOHS Additional Tissue Blocks",
        ])
        risk = classify_mohs_risk(location or "")
        is_high_risk = (risk == "high_risk")

        hr_kws = ["head", "neck", "face", "scalp", "ear", "eyelid", "nose", "lip", "hand", "foot", "genitalia"]
        tr_kws = ["trunk", "extremit", "arm", "leg"]

        filtered = [
            r for r in rows
            if any(k in (r.get("description") or "").lower()
                   for k in (hr_kws if is_high_risk else tr_kws))
        ]
        logger.info(f"Mohs candidates: {len(filtered or rows)}")
        return filtered or rows

    # ── Closure ──────────────────────────────────────────────────────────────

    async def closure_filter(
        self,
        size: float,
        location: str,
        ctype: str,
    ) -> List[Dict]:
        """
        Fetch closure candidates by proName (not by code prefix).
        Location filtering uses match_desc_by_location() from base.py.
        """
        ctype = (ctype or "").lower()

        proname_map = {
            "simple":       ["Simple Closure"],
            "intermediate": ["Layered Closure"],
            "complex":      ["Complex Closure"],
            "adjacent":     ["Adjacent Tissue Transfer"],
        }
        pro_names = proname_map.get(ctype)
        if not pro_names:
            logger.warning(f"closure_filter: unknown closure type '{ctype}'")
            return []

        rows = await self._fetch_by_pronames(pro_names)

        # Location filter using centralized keyword sets from base.py
        pool = match_desc_by_location(rows, location or "trunk") if location else rows

        # Size filter with epsilon tolerance
        filtered = [r for r in pool if _size_match(r, size)]

        # Fallback 1: type + size, ignore location
        if not filtered:
            logger.warning("closure_filter: no strict match — relaxing location")
            filtered = [r for r in rows if _size_match(r, size)]

        # Fallback 2: return all type-matched rows
        if not filtered:
            logger.warning("closure_filter: no size match — returning all type rows")
            filtered = rows

        for r in filtered:
            logger.info(
                f"Candidate → code={r['code']} | desc={r['description']} | "
                f"range=({r.get('minSize')},{r.get('maxSize')}) | parent={r.get('associatedWithProCode')}"
            )
        return filtered

    # ── Shave Removal ─────────────────────────────────────────────────────────

    async def shave_removal_filter(
        self,
        location_group: str,
        size: Optional[float],
    ) -> List[Dict]:
        rows = await self._fetch_by_proname("Shave Removal")

        # Location filter uses centralized keywords from base.py
        pool = match_desc_by_location(rows, location_group or "trunk")

        if size is not None:
            filtered = [r for r in pool if _size_match(r, size)]
        else:
            # No size — return the smallest code for this location group
            filtered = sorted(pool, key=lambda r: float(r.get("maxSize") or 0))
            filtered = [filtered[0]] if filtered else []

        logger.info(f"Shave removal candidates: {len(filtered)}")
        return filtered

    # ── SRT ──────────────────────────────────────────────────────────────────

    async def srt_filter(self, srt_section: Dict) -> List[Dict]:
        """
        Select SRT codes by proName — no hardcoded CPT code numbers.

        SRT proNames in the CSV:
          'Surface radiation therapy (SRT); planning'            → 77436
          'Surface radiation therapy (SRT); superficial delivery'→ 77437
          'Surface radiation therapy (SRT); orthovoltage delivery'→77438
          'Surface radiation therapy (SRT); ultrasound guidance' → 77439
        """
        kv           = srt_section.get("kv")
        ultrasound   = srt_section.get("ultrasound")
        images       = srt_section.get("images_present")

        rows = await self._fetch_by_pronames([
            "Surface radiation therapy (SRT); planning",
            "Surface radiation therapy (SRT); superficial delivery",
            "Surface radiation therapy (SRT); orthovoltage delivery",
            "Surface radiation therapy (SRT); ultrasound guidance",
        ])
        rows_by_proname = {r.get("proName", "").lower(): r for r in rows}

        selected: List[Dict] = []

        # Planning code — always included
        planning = rows_by_proname.get("surface radiation therapy (srt); planning")
        if planning:
            selected.append(planning)

        # Delivery code — based on kV
        if kv is not None:
            if kv <= SRT_KV_BOUNDARY:
                delivery = rows_by_proname.get("surface radiation therapy (srt); superficial delivery")
            else:
                delivery = rows_by_proname.get("surface radiation therapy (srt); orthovoltage delivery")
            if delivery:
                selected.append(delivery)

        # Ultrasound guidance add-on
        if ultrasound and images:
            guidance = rows_by_proname.get("surface radiation therapy (srt); ultrasound guidance")
            if guidance:
                selected.append(guidance)
            logger.info("SRT: ultrasound guidance add-on included")
        else:
            logger.info("SRT: ultrasound guidance not indicated")

        logger.info(f"SRT codes: {[r['code'] for r in selected]}")
        return selected

    # ── Debridement ──────────────────────────────────────────────────────────

    async def debridement_filter(self, section: Dict) -> List[Dict]:
        """
        Select debridement codes by proName — no hardcoded CPT code numbers.
        Matches by description keywords for depth/nail/dermatologic variants.
        """
        depth        = (section.get("depth") or "").lower()
        nail         = section.get("nail")
        dermatologic = section.get("dermatologic")
        is_wound     = section.get("is_wound")
        quantity     = int(section.get("quantity") or 1)

        rows = await self._fetch_by_proname("Debridement")

        def _by_keyword(*keywords: str) -> List[Dict]:
            return [
                r for r in rows
                if any(k in (r.get("description") or "").lower() for k in keywords)
            ]

        if nail:
            # Nail avulsion: ≤5 = first code, >5 = additional code
            candidates = _by_keyword("nail")
            candidates.sort(key=lambda r: float(r.get("maxQty") or 0))
            result = [candidates[0]] if quantity <= 5 else candidates[1:2] if len(candidates) > 1 else candidates
            logger.info(f"Debridement nail: {[r['code'] for r in result]}")
            return result

        if dermatologic and not is_wound:
            result = _by_keyword("epidermis")
            logger.info(f"Debridement dermatologic: {[r['code'] for r in result]}")
            return result

        # Wound debridement by depth
        keyword_map = {
            "partial":       ["partial-thickness", "partial thickness", "epidermis"],
            "full":          ["full-thickness", "full thickness", "dermis"],
            "subcutaneous":  ["subcutaneous"],
        }
        keywords = keyword_map.get(depth, ["partial-thickness", "epidermis"])
        result = _by_keyword(*keywords)

        if not result:
            logger.warning(f"Debridement: no match for depth={depth!r} — returning all candidates")
            result = rows

        logger.info(f"Debridement candidates: {[r['code'] for r in result]}")
        return result

    # ── XTRAC ────────────────────────────────────────────────────────────────

    async def xtrac_filter(self, total_area: Optional[float]) -> List[Dict]:
        rows = await self._fetch_by_proname("Xtrac Laser Treatment")

        if total_area is None:
            # No area — return smallest code as fallback
            result = sorted(rows, key=lambda r: float(r.get("minSize") or 0))
            return [result[0]] if result else rows

        filtered = [r for r in rows if _size_match(r, total_area)]
        if not filtered:
            logger.warning("XTRAC: no area range match — returning all candidates")
            filtered = rows

        logger.info(f"XTRAC candidates: {[r['code'] for r in filtered]}")
        return filtered

    # ── IPL ──────────────────────────────────────────────────────────────────

    async def ipl_filter(self, section: Dict) -> List[Dict]:
        rows = await self._fetch_by_proname("Intense Pulsed Light")
        method         = (section.get("method") or "").lower()
        treatment_area = section.get("treatment_area")

        if method:
            tokens = [t for t in method.split() if len(t) > MIN_METHOD_TOKEN_LENGTH]
            matched = [
                r for r in rows
                if any(t in (r.get("description") or "").lower() for t in tokens)
            ]
            if matched:
                return matched

        if treatment_area is not None:
            filtered = [r for r in rows if _size_match(r, float(treatment_area))]
            if filtered:
                return filtered

        # Default: smallest code (first by minSize)
        fallback = sorted(rows, key=lambda r: float(r.get("minSize") or 0))
        logger.info(f"IPL fallback: {[r['code'] for r in fallback[:1]]}")
        return fallback[:1] if fallback else rows

    # ── Filler Material ───────────────────────────────────────────────────────

    async def filler_material_filter(self, section: Dict) -> List[Dict]:
        rows = await self._fetch_by_proname("Filler Material")
        quantity_used = section.get("used_quantity")

        if quantity_used is not None:
            filtered = [r for r in rows if _size_match(r, float(quantity_used))]
            if filtered:
                return filtered

        logger.info("Filler material: returning all candidates")
        return rows

    # ── Filler ────────────────────────────────────────────────────────────────

    async def filler_filter(self, section: Dict) -> List[Dict]:
        rows = await self._fetch_by_proname("Filler")
        method = (section.get("method") or "").lower()

        if method:
            tokens = [t for t in method.split() if len(t) > MIN_METHOD_TOKEN_LENGTH]
            matched = [
                r for r in rows
                if any(t in (r.get("description") or "").lower() for t in tokens)
            ]
            if matched:
                return matched

        # Default: first code
        fallback = sorted(rows, key=lambda r: r.get("code") or "")
        logger.info(f"Filler fallback: {[r['code'] for r in fallback[:1]]}")
        return fallback[:1] if fallback else rows

    # ── Chemical Peel ─────────────────────────────────────────────────────────

    async def chemical_peel_filter(self, section: Dict) -> List[Dict]:
        peel_type = (section.get("type") or "").lower()
        method    = (section.get("method") or "").lower()
        choice    = (section.get("choice") or "").lower()

        proname_map = {
            "chemical_peel_epidermal": "Chemical Peel Epidermal",
            "chemical_peel_dermal":    "Chemical Peel Dermal",
        }
        pro_name = proname_map.get(peel_type, "Chemical Peel")

        rows = await self._fetch_by_proname(pro_name)

        # Try to match by method or choice keyword in description
        for keyword in (method, choice):
            if keyword:
                matched = [r for r in rows if keyword in (r.get("description") or "").lower()]
                if matched:
                    return matched

        # Default: all codes for this peel type
        logger.info(f"Chemical peel: returning all {len(rows)} candidates for {pro_name!r}")
        return rows

    # ── Laser ─────────────────────────────────────────────────────────────────

    async def laser_treatment_filter(self, section: Dict, full_procedure_text: str) -> List[Dict]:
        rows = await self._fetch_by_proname("Laser Treatment")
        method         = (section.get("method") or "").lower()
        procedure_text = (full_procedure_text or "").lower()

        # Step 1: match by method keyword in code description
        if method:
            normalized = re.sub(r"(laser|treatment|therapy)", "", method).strip()
            matched = [
                r for r in rows
                if normalized in re.sub(r"(laser|treatment)", "", (r.get("description") or "").lower())
            ]
            if matched:
                return matched

        # Step 2: match description keywords against full procedure text
        matched = []
        for r in rows:
            desc_clean = re.sub(r"(laser|treatment)", "", (r.get("description") or "").lower())
            keywords = [k for k in desc_clean.split() if len(k) > MIN_METHOD_TOKEN_LENGTH]
            if any(k in procedure_text for k in keywords):
                matched.append(r)
        if matched:
            return matched

        # Step 3: return first code as fallback
        fallback = sorted(rows, key=lambda r: r.get("code") or "")
        logger.info(f"Laser fallback: {[r['code'] for r in fallback[:1]]}")
        return fallback[:1] if fallback else rows
