# app/services/engine_runner.py

from loguru import logger

from app.graph.langgraph_builder import build_graph


def _format_cpt(cpt: dict, confirmed_codes: set | None = None) -> dict:
    """Reshape a CPT code dict to the standard API output format."""
    code = str(cpt.get("code", "")).strip()
    is_confirmed = confirmed_codes is not None and code in confirmed_codes
    return {
        "cpt_code": code,
        "modifier": cpt.get("modifier"),
        "dxcode": cpt.get("linked_dx", []),
        "qty": cpt.get("quantity", "1"),
        "charge_per_unit": "Yes" if cpt.get("charge_per_unit") else "No",
        "confidence": "confirmed" if is_confirmed else cpt.get("confidence", "candidate"),
        "source": "selector" if is_confirmed else cpt.get("source", "llm"),
        "reasoning": _format_reasoning(cpt.get("reasoning", {})),
    }


def _format_reasoning(r: dict) -> dict:
    """Surface all reasoning fields including the new supporting_evidence list."""
    if not r:
        return {}
    return {
        "justification":        r.get("justification", ""),
        "supporting_evidence":  r.get("supporting_evidence", []),
        "modifier_justification": r.get("modifier_justification"),
        "dx_justification":     r.get("dx_justification"),
        "confidence_assessment": r.get("confidence_assessment", ""),
        "flag":                 r.get("flag"),
    }


def _format_em(em: dict) -> dict | None:
    """Reshape an E/M code dict. Returns None when no E/M code was assigned."""
    code = em.get("code", "")
    if not code:
        return None
    return {
        "cpt_code": code,
        "modifier": em.get("modifier"),
        "dxcode": em.get("linked_dx", []),
        "qty": "1",
        "charge_per_unit": "Yes",
        "reasoning": _format_reasoning(em.get("reasoning", {})),
    }


def _format_parse_source(parse_source: dict) -> dict:
    """Return only sections that were actually found (not empty)."""
    return {k: v for k, v in parse_source.items() if v != "empty"}


class MedicalCodingService:

    def __init__(self):
        self.graph = build_graph()

    async def process(self, note_id: int) -> dict:
        try:
            logger.info(f"Running pipeline for note {note_id}")

            state = await self.graph.ainvoke({"note_id": note_id})

            llm_output = state.get("llm_output", {})
            codes = llm_output.get("codes", {})
            parsed = state.get("parsed", {})

            # Build a set of selector-confirmed codes so the output
            # correctly reflects which codes came from deterministic rules
            # vs the LLM (the LLM output itself never carries this tag).
            confirmed_codes = {
                str(c.get("code", "")).strip()
                for c in state.get("candidates", [])
                if c.get("confidence") == "confirmed"
            }

            procedures = [
                _format_cpt(c, confirmed_codes)
                for c in codes.get("cpt_codes", [])
                if c.get("code")
            ]
            em = _format_em(codes.get("em_code", {}))

            # Unresolved procedures — surfaced with suggested_resolution when
            # the deterministic engine can still select a code in the boundary zone.
            unresolved = parsed.get("unresolved_procedures") or []
            unresolved_out = [
                {
                    "description": u.get("description", ""),
                    "reason": u.get("reason", "unknown"),
                    # Suggested code when available — billing team approves rather than recodes
                    "suggested_resolution": u.get("suggested_resolution"),
                }
                for u in unresolved
            ]

            # Parse source — tells caller which sections came from the regex
            # parser vs the LLM extraction fallback.
            parse_source = _format_parse_source(state.get("parse_source") or {})

            return {
                "note_id": note_id,
                "patient_summary": llm_output.get("patient_summary", ""),
                "procedure": procedures,
                "em": em,
                "overall_assessment": llm_output.get("overall_assessment", ""),
                "audit_flags": llm_output.get("audit_flags", []),
                "unresolved_procedures": unresolved_out,
                "parse_source": parse_source,
                "web_refs_used": len(state.get("web_refs") or []),
            }

        except Exception as e:
            logger.exception(f"Pipeline failed for note {note_id}: {e}")
            raise
