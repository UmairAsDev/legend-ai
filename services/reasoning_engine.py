# services/reasoning_engine.py
"""
Reasoning engine — produces structured justification for every coding decision.

Called after all deterministic enforcement is complete.  It does NOT change
any codes.  It produces a justification dict attached to each code in the
output so that every decision is traceable, auditable, and explainable.

Architecture
------------
Deterministic layer   → selects codes (no hallucination)
Reasoning layer (LLM) → explains WHY each code was selected, cites the note,
                         flags anything unsupported by documentation
"""

from typing import Any, Dict, List
from loguru import logger

from llm_layer.llm_client import LLMClient
from llm_layer.reasoning_prompt import build_reasoning_prompt


# One shared client at module level — reuses the same connection
_llm: LLMClient | None = None


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


# =============================================================
# CONTEXT BUILDERS
# =============================================================

def _build_assigned_codes(cpt_codes: List[Dict]) -> List[Dict]:
    """
    Slim each CPT code dict down to only what the reasoning LLM needs:
    code, description, quantity, modifier, linked_dx, and the selection_data
    that the selector attached (what data drove the decision).
    """
    return [
        {
            "code": c.get("code"),
            "description": c.get("description"),
            "quantity": c.get("quantity"),
            "modifier": c.get("modifier"),
            "linked_dx": c.get("linked_dx", []),
            "confidence": c.get("confidence", "candidate"),
            "source": c.get("source"),
            "selection_data": c.get("selection_data", {}),
        }
        for c in cpt_codes
        if c.get("code")
    ]


def _build_modifier_decisions(cpt_codes: List[Dict], em_code: Dict | None) -> List[Dict]:
    """
    Collect every modifier assignment with the code it was applied to,
    so the reasoning LLM can explain each one.
    """
    decisions = []
    for c in cpt_codes:
        if c.get("modifier"):
            decisions.append({
                "code": c.get("code"),
                "modifier": c.get("modifier"),
                "source": c.get("source"),
            })
    if em_code and em_code.get("modifier"):
        decisions.append({
            "code": em_code.get("code"),
            "modifier": em_code.get("modifier"),
            "source": "em_enforcement",
        })
    return decisions


def _build_em_signals(parsed: Dict) -> Dict:
    """Extract E/M selection signals from the parsed dict for the reasoning context."""
    em_data = parsed.get("em_data", {})
    return {
        "patient_type": em_data.get("patient_type"),
        "encounter_time_min": em_data.get("encounter_time"),
        "em_level_explicit": em_data.get("em_level"),
        "mdm_level": em_data.get("mdm_level"),
        "explicit_em_code": em_data.get("explicit_em_code"),
    }


# =============================================================
# REASONING ATTACHMENT
# =============================================================

def _attach_reasoning(
    cpt_codes: List[Dict],
    em_code: Dict | None,
    reasoning_output: Dict,
) -> tuple[List[Dict], Dict | None]:
    """
    Merge the LLM-generated reasoning into the code dicts.
    Matches on code value — safe because codes are unique in the output.
    """
    reasoning_by_code: Dict[str, Dict] = {}
    for r in reasoning_output.get("cpt_reasoning", []):
        code = r.get("code") if isinstance(r, dict) else getattr(r, "code", None)
        if code:
            reasoning_by_code[str(code)] = r if isinstance(r, dict) else r.dict()

    for cpt in cpt_codes:
        code = str(cpt.get("code", ""))
        cpt["reasoning"] = reasoning_by_code.get(code, {})

    if em_code and em_code.get("code"):
        em_r = reasoning_output.get("em_reasoning")
        em_code["reasoning"] = em_r if isinstance(em_r, dict) else (em_r.dict() if em_r else {})

    return cpt_codes, em_code


# =============================================================
# MAIN ENTRY POINT
# =============================================================

async def generate_reasoning(
    llm_output: Dict[str, Any],
    parsed: Dict[str, Any],
    note: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate structured reasoning for every code in llm_output.

    Attaches a `reasoning` field to each CPT and E/M code:
      {
        "justification": "...",
        "modifier_justification": "...",
        "dx_justification": "...",
        "confidence_assessment": "supported | partially_supported | unsupported",
        "flag": null | "concern message"
      }

    Also returns overall_assessment and audit_flags at the top level.
    """
    try:
        codes = llm_output.get("codes", {})
        cpt_codes = codes.get("cpt_codes", [])
        em_code = codes.get("em_code") or {}

        if not cpt_codes and not em_code.get("code"):
            logger.info("Reasoning skipped — no codes assigned")
            llm_output["overall_assessment"] = "No codes assigned for this note."
            llm_output["audit_flags"] = []
            return llm_output

        assigned = _build_assigned_codes(cpt_codes)
        modifier_decisions = _build_modifier_decisions(cpt_codes, em_code)
        em_signals = _build_em_signals(parsed)

        _, parser, formatted_prompt = build_reasoning_prompt(
            assigned_codes=assigned,
            em_code=em_code if em_code.get("code") else None,
            em_signals=em_signals,
            modifier_decisions=modifier_decisions,
            note=note,
        )

        logger.info(f"Calling reasoning LLM for {len(assigned)} CPT + EM={bool(em_code.get('code'))}")
        raw = await _get_llm().generate_response(formatted_prompt, parser=parser)

        if not isinstance(raw, dict):
            raw = raw.dict() if hasattr(raw, "dict") else {}

        updated_cpt, updated_em = _attach_reasoning(cpt_codes, em_code, raw)
        llm_output["codes"]["cpt_codes"] = updated_cpt
        llm_output["codes"]["em_code"] = updated_em or em_code
        llm_output["overall_assessment"] = raw.get("overall_assessment", "")

        # Merge: preserve flags injected by earlier pipeline steps (e.g. unresolved
        # procedures from billing_params) and append reasoning LLM flags on top.
        existing_flags = llm_output.get("audit_flags") or []
        reasoning_flags = raw.get("audit_flags") or []
        llm_output["audit_flags"] = existing_flags + [
            f for f in reasoning_flags if f not in existing_flags
        ]

        flags = llm_output["audit_flags"]
        if flags:
            logger.warning(f"Audit flags raised: {flags}")
        else:
            logger.info("Reasoning complete — no audit flags")

        return llm_output

    except Exception as e:
        # Reasoning failure must never block the pipeline
        logger.exception(f"Reasoning engine failed: {e}")
        llm_output.setdefault("overall_assessment", "Reasoning unavailable.")
        llm_output.setdefault("audit_flags", [])
        return llm_output
