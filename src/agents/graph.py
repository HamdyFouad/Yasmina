"""LangGraph orchestration for the Yasmina tender workflow.

Graph:
    enrich (optional LLM) → match catalogue → price offer → approval gate
        ├─ approved → GAEB X84 export (when configured)
        └─ pending / invalid → stop for human review
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agents.nodes import (
    approval_gate,
    enrich_specs,
    export_offer,
    match_catalog,
    price_offer,
    route_after_approval,
    route_after_price,
)
from src.agents.state import AgentContext, AgentState, initial_state

__all__ = ["build_agent_graph", "run_agent"]


def build_agent_graph(context: AgentContext) -> Any:
    """Compile the deterministic workflow with injected services."""

    graph = StateGraph(AgentState)
    graph.add_node("enrich", lambda state: enrich_specs(state, context))
    graph.add_node("match", lambda state: match_catalog(state, context))
    graph.add_node("price", lambda state: price_offer(state, context))
    graph.add_node("approval", lambda state: approval_gate(state, context))
    graph.add_node("export", lambda state: export_offer(state, context))

    graph.add_edge(START, "enrich")
    graph.add_edge("enrich", "match")
    graph.add_edge("match", "price")
    graph.add_conditional_edges(
        "price",
        route_after_price,
        {"approval": "approval", "stop": END},
    )
    graph.add_conditional_edges(
        "approval",
        route_after_approval,
        {"export": "export", "stop": END},
    )
    graph.add_edge("export", END)
    return graph.compile()


def run_agent(state: AgentState, context: AgentContext) -> AgentState:
    """Invoke one workflow pass and return the final state."""

    return build_agent_graph(context).invoke(state)  # type: ignore[return-value]
