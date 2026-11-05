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
    route_after_triage,
    triage_tender,
)
from src.agents.state import AgentContext, AgentState, initial_state

__all__ = ["build_agent_graph", "run_agent", "run_agent_async"]


def build_agent_graph(context: AgentContext, *, checkpointer: Any | None = None) -> Any:
    """Compile the deterministic workflow with injected services.

    When ``checkpointer`` is provided, LangGraph stores the state machine's
    checkpoints through that saver. The API passes a PostgreSQL-backed saver.
    """

    graph = StateGraph(AgentState)
    graph.add_node("triage", lambda state: triage_tender(state, context))
    graph.add_node("enrich", lambda state: enrich_specs(state, context))
    graph.add_node("match", lambda state: match_catalog(state, context))
    graph.add_node("price", lambda state: price_offer(state, context))
    graph.add_node("approval", lambda state: approval_gate(state, context))
    graph.add_node("export", lambda state: export_offer(state, context))

    graph.add_edge(START, "triage")
    graph.add_conditional_edges(
        "triage",
        route_after_triage,
        {"enrich": "enrich", "stop": END},
    )
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
    return graph.compile(checkpointer=checkpointer)


def run_agent(state: AgentState, context: AgentContext) -> AgentState:
    """Invoke one workflow pass synchronously (tests/offline tooling)."""

    return build_agent_graph(context).invoke(state)  # type: ignore[return-value]


async def run_agent_async(
    state: AgentState,
    context: AgentContext,
    *,
    checkpointer: Any | None = None,
    config: dict[str, Any] | None = None,
) -> AgentState:
    """Invoke one workflow pass asynchronously, optionally with checkpointing."""

    graph = build_agent_graph(context, checkpointer=checkpointer)
    return await graph.ainvoke(state, config=config or {})  # type: ignore[return-value]
