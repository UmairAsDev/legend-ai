# services/web_lookup.py
"""
Controlled web search for medical billing reference lookups.

Design:
- The pipeline decides WHEN to search (trigger conditions).
- The pipeline constructs WHAT to search (query templates).
- The LLM does NOT autonomously choose searches.
- Maximum 2 searches per note to control latency.
- Results are injected as read-only context into CoT Step 3.
- Failures degrade gracefully — search is never blocking.

Search backend priority:
  1. Tavily (set TAVILY_API_KEY env var) — best for factual medical queries
  2. DuckDuckGo — free, no key needed, lower quality fallback
"""

import asyncio
import os
from typing import Any, Dict, List, Optional
from loguru import logger


# ─────────────────────────────────────────────────────────────
# TRIGGER CONDITIONS
# ─────────────────────────────────────────────────────────────

# Excision CPT size boundaries in cm — flag if within ±BOUNDARY_TOLERANCE
_EXCISION_BOUNDARIES = [0.5, 1.0, 2.0, 3.0, 4.0]
_BOUNDARY_TOLERANCE = 0.3

# Procedure types always searched (LCD-governed or payer-variable)
_ALWAYS_SEARCH = {"srt", "ipl"}


def _is_boundary_case(size: Optional[float], boundaries: List[float]) -> bool:
    if size is None:
        return False
    return any(abs(size - b) <= _BOUNDARY_TOLERANCE for b in boundaries)


# ─────────────────────────────────────────────────────────────
# QUERY TEMPLATES
# ─────────────────────────────────────────────────────────────

_QUERY_TEMPLATES: Dict[str, str] = {
    "excision_boundary": (
        "CPT code excision {lesion_type} lesion {location} {size}cm "
        "AMA dermatology 2024 size range guidelines"
    ),
    "srt": (
        "CPT 77436 77437 77438 superficial radiation therapy dermatology "
        "Medicare LCD coverage requirements 2024"
    ),
    "ipl": (
        "CPT code intense pulsed light IPL dermatology {method} "
        "coverage criteria payer billing 2024"
    ),
    "unknown_procedure": (
        "{description} dermatology CPT billing code 2024 AMA guidelines"
    ),
    "modifier_edge": (
        "CPT modifier {modifier} dermatology {code1} {code2} NCCI edit 2024"
    ),
    "destruction_boundary": (
        "CPT code destruction {destruction_type} dermatology quantity {quantity} "
        "AMA 2024"
    ),
}


def _build_query(trigger_type: str, params: Dict[str, Any]) -> str:
    template = _QUERY_TEMPLATES.get(trigger_type, "")
    if not template:
        return ""
    try:
        return template.format(**{k: (v or "") for k, v in params.items()})
    except KeyError:
        return template.split("{")[0].strip()


# ─────────────────────────────────────────────────────────────
# WEB LOOKUP SERVICE
# ─────────────────────────────────────────────────────────────

class WebLookupService:

    MAX_SEARCHES_PER_NOTE = 2
    MAX_RESULT_CHARS = 600  # trim each result to bound token usage

    def __init__(self):
        self._tavily_key = os.getenv("TAVILY_API_KEY")
        self._backend = "tavily" if self._tavily_key else "duckduckgo"
        logger.info(f"WebLookupService initialised — backend={self._backend}")

    # ─────────────────────────────────────────────────────────
    # TRIGGER EVALUATION
    # ─────────────────────────────────────────────────────────

    def should_search(self, parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Evaluate parsed sections and return a list of search trigger dicts.
        Each dict has keys: trigger_type, query_params, label.
        Capped at MAX_SEARCHES_PER_NOTE.
        """
        triggers: List[Dict[str, Any]] = []

        # Excision boundary cases
        for sec in parsed.get("excision_sections", []):
            if _is_boundary_case(sec.get("size"), _EXCISION_BOUNDARIES):
                triggers.append({
                    "trigger_type": "excision_boundary",
                    "query_params": {
                        "lesion_type": sec.get("lesion_type") or "skin",
                        "location": sec.get("location") or "unspecified",
                        "size": sec.get("size"),
                    },
                    "label": f"excision boundary {sec.get('size')}cm",
                })

        # SRT — always search (LCD requirements)
        if parsed.get("has_srt"):
            triggers.append({
                "trigger_type": "srt",
                "query_params": {},
                "label": "SRT LCD requirements",
            })

        # IPL — always search (payer-variable)
        if parsed.get("has_ipl"):
            for sec in parsed.get("ipl_sections", []):
                triggers.append({
                    "trigger_type": "ipl",
                    "query_params": {"method": sec.get("method") or ""},
                    "label": f"IPL {sec.get('method') or 'unspecified method'}",
                })
                break  # one search per note for IPL

        # Unresolved procedures — flag for unknown procedure search
        for proc in parsed.get("unresolved_procedures", []):
            if proc.get("reason") == "unknown":
                triggers.append({
                    "trigger_type": "unknown_procedure",
                    "query_params": {"description": proc.get("description", "")[:100]},
                    "label": f"unknown: {proc.get('description', '')[:60]}",
                })

        # Deduplicate by trigger_type and cap
        seen_types: set = set()
        deduped: List[Dict] = []
        for t in triggers:
            key = t["trigger_type"]
            if key not in seen_types:
                seen_types.add(key)
                deduped.append(t)
            if len(deduped) >= self.MAX_SEARCHES_PER_NOTE:
                break

        if deduped:
            logger.info(f"Web search triggers: {[t['label'] for t in deduped]}")
        else:
            logger.info("No web search triggers for this note")

        return deduped

    # ─────────────────────────────────────────────────────────
    # SEARCH EXECUTION
    # ─────────────────────────────────────────────────────────

    async def search(self, triggers: List[Dict[str, Any]]) -> List[str]:
        """
        Execute searches for all triggers concurrently.
        Returns list of trimmed result strings.
        Failures return empty string (never raises).
        """
        if not triggers:
            return []

        tasks = [self._search_one(t) for t in triggers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        refs: List[str] = []
        for t, result in zip(triggers, results):
            if isinstance(result, Exception):
                logger.warning(f"Web search failed for '{t['label']}': {result}")
            elif result:
                refs.append(f"[{t['label']}]\n{result}")

        return refs

    async def _search_one(self, trigger: Dict[str, Any]) -> str:
        query = _build_query(trigger["trigger_type"], trigger["query_params"])
        if not query:
            return ""

        logger.info(f"Searching: {query[:80]}...")

        try:
            if self._backend == "tavily":
                return await self._tavily_search(query)
            else:
                return await self._duckduckgo_search(query)
        except Exception as e:
            logger.warning(f"Search error: {e}")
            return ""

    async def _tavily_search(self, query: str) -> str:
        try:
            from tavily import TavilyClient
        except ImportError:
            logger.warning("tavily-python not installed — falling back to DuckDuckGo")
            return await self._duckduckgo_search(query)

        loop = asyncio.get_event_loop()

        def _sync_search():
            client = TavilyClient(api_key=self._tavily_key)
            response = client.search(
                query=query,
                search_depth="basic",
                max_results=2,
                include_answer=True,
            )
            # Prefer the Tavily direct answer, then first result content
            if response.get("answer"):
                return response["answer"][:self.MAX_RESULT_CHARS]
            results = response.get("results", [])
            if results:
                return results[0].get("content", "")[:self.MAX_RESULT_CHARS]
            return ""

        return await loop.run_in_executor(None, _sync_search)

    async def _duckduckgo_search(self, query: str) -> str:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("duckduckgo_search not installed — web lookup unavailable")
            return ""

        loop = asyncio.get_event_loop()

        def _sync_search():
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=2))
            if not results:
                return ""
            # Combine title + body of first result
            r = results[0]
            text = f"{r.get('title', '')}: {r.get('body', '')}"
            return text[:self.MAX_RESULT_CHARS]

        return await loop.run_in_executor(None, _sync_search)
