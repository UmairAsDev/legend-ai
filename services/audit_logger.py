# services/audit_logger.py
"""
Structured audit trail for all medical coding decisions.

Writes one JSONL entry per note to logs/audit.jsonl.
Each entry records which codes were assigned, their sources
(selector-confirmed vs LLM-selected), E/M code, modifiers,
and which MDM / time signal drove E/M selection.

Required for HIPAA 164.312(b) audit controls and to support
coding appeals or audits by payers.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_LOG_PATH = Path("logs/audit.jsonl")


def _ensure_log_dir() -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def log_coding_decision(
    note_id: int,
    llm_output: Dict[str, Any],
    candidates: List[Dict],
    parsed: Dict[str, Any],
) -> None:
    """
    Write one structured audit record for a processed note.

    Fields logged:
    - timestamp (UTC ISO-8601)
    - note_id
    - cpt_codes: list of {code, quantity, modifier, confidence, source, linked_dx}
    - em_code: {code, modifier, linked_dx, selection_method}
    - em_signals: what drove E/M selection (time / level / mdm / explicit)
    - confirmed_count / candidate_count: how many codes came from each path
    """
    _ensure_log_dir()

    codes = llm_output.get("codes", {})
    cpt_codes = codes.get("cpt_codes", [])
    em_code = codes.get("em_code", {})
    em_data = parsed.get("em_data", {})

    # Summarise candidate sources
    confirmed = [c for c in candidates if c.get("confidence") == "confirmed"]
    candidate = [c for c in candidates if c.get("confidence") != "confirmed"]

    # Determine what drove E/M selection
    em_method = "none"
    if em_data.get("explicit_em_code"):
        em_method = "explicit_code"
    elif em_data.get("encounter_time"):
        em_method = f"time_{em_data['encounter_time']}min"
    elif em_data.get("em_level"):
        em_method = f"level_{em_data['em_level']}"
    elif em_data.get("mdm_level"):
        em_method = f"mdm_level_{em_data['mdm_level']}"
    elif em_code.get("code"):
        em_method = "llm"

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note_id": note_id,
        "cpt_codes": [
            {
                "code": c.get("code"),
                "quantity": c.get("quantity"),
                "modifier": c.get("modifier"),
                "linked_dx": c.get("linked_dx", []),
                "confidence": c.get("confidence"),
                "source": c.get("source"),
                "selection_data": c.get("selection_data", {}),
                "reasoning": c.get("reasoning", {}),
            }
            for c in cpt_codes
        ],
        "em_code": {
            "code": em_code.get("code", ""),
            "modifier": em_code.get("modifier"),
            "linked_dx": em_code.get("linked_dx", []),
            "selection_method": em_method,
            "reasoning": em_code.get("reasoning", {}),
        },
        "em_signals": {
            "patient_type": em_data.get("patient_type"),
            "encounter_time": em_data.get("encounter_time"),
            "em_level": em_data.get("em_level"),
            "mdm_level": em_data.get("mdm_level"),
        },
        "retrieval": {
            "confirmed_codes": len(confirmed),
            "candidate_codes": len(candidate),
            "confirmed_list": [c.get("code") for c in confirmed],
        },
        "overall_assessment": llm_output.get("overall_assessment", ""),
        "audit_flags": llm_output.get("audit_flags", []),
        "total_cpt": len(cpt_codes),
    }

    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        # HIPAA 164.312(b): audit trail failures must be surfaced loudly.
        # Pipeline continues so the patient visit is not blocked, but this
        # must be investigated — a missing audit record is a compliance gap.
        from loguru import logger
        logger.error(f"AUDIT LOG WRITE FAILED for note {note_id}: {e} — compliance gap, investigate immediately")
