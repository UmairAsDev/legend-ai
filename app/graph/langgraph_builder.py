# app/graph/langgraph_builder.py

from typing import Dict, List, TypedDict

from langgraph.graph import StateGraph

from app.services.medical_engine import CodingNodes


class CodingState(TypedDict, total=False):
    note_id: int
    raw_note: dict
    cleaned_note: dict
    parsed: dict            # merged: regex + LLM extraction after billing_params
    em_data: dict
    candidates: list
    llm_output: dict
    clinical_summary: str   # CoT Step 1 free-text reasoning output
    web_refs: List[str]     # reference snippets injected into Step 3 prompt
    parse_source: Dict[str, str]  # per-section: "regex" | "llm" | "empty"


def build_graph():
    nodes = CodingNodes()
    graph = StateGraph(CodingState)

    graph.add_node("fetch", nodes.fetch)
    graph.add_node("clean", nodes.clean)
    graph.add_node("parse", nodes.parse)
    graph.add_node("clinical_read", nodes.clinical_read)       # CoT Step 1
    graph.add_node("billing_params", nodes.billing_params)     # CoT Step 2
    graph.add_node("web_lookup", nodes.web_lookup_node)        # conditional search
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("llm", nodes.llm_call)                      # CoT Step 3
    graph.add_node("validate", nodes.validate)                 # Billing integrity rules
    graph.add_node("em_modifiers", nodes.assign_em)
    graph.add_node("reasoning", nodes.reason)

    graph.set_entry_point("fetch")
    graph.add_edge("fetch", "clean")
    graph.add_edge("clean", "parse")
    graph.add_edge("parse", "clinical_read")
    graph.add_edge("clinical_read", "billing_params")
    graph.add_edge("billing_params", "web_lookup")
    graph.add_edge("web_lookup", "retrieve")
    graph.add_edge("retrieve", "llm")
    graph.add_edge("llm", "validate")
    graph.add_edge("validate", "em_modifiers")
    graph.add_edge("em_modifiers", "reasoning")
    graph.set_finish_point("reasoning")

    return graph.compile()
