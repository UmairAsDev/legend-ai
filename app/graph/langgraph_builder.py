# app/graph/langgraph_builder.py

from typing import TypedDict
from langgraph.graph import StateGraph

from app.services.medical_engine import CodingNodes


class CodingState(TypedDict, total=False):
    note_id: int
    raw_note: dict
    cleaned_note: dict
    query_text: str
    embedding: list
    candidates: list
    reranked: list
    llm_output: dict


def build_graph():
    nodes = CodingNodes()

    graph = StateGraph(CodingState)

    graph.add_node("fetch", nodes.fetch)
    graph.add_node("clean", nodes.clean)
    graph.add_node("query", nodes.query)
    graph.add_node("embed", nodes.embed)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("rerank", nodes.rerank)
    graph.add_node("llm", nodes.llm_call)

    graph.set_entry_point("fetch")

    graph.add_edge("fetch", "clean")
    graph.add_edge("clean", "query")
    graph.add_edge("query", "embed")
    graph.add_edge("embed", "retrieve")
    graph.add_edge("retrieve", "rerank")
    graph.add_edge("rerank", "llm")

    graph.set_finish_point("llm")

    return graph.compile()