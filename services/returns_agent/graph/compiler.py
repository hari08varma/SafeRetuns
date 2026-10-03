"""Compile a validated GraphSpec (JSON) into an executable LangGraph StateGraph.

The JSON graph is the source of truth; LangGraph provides execution, checkpointing
and pause/resume. Business logic lives in the node handlers, not in this module.
"""

import operator
from collections.abc import Callable
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from returns_agent.graph.conditions import evaluate
from returns_agent.graph.schema import GraphSpec, NodeSpec, validate_graph


def merge_facts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class CaseState(TypedDict, total=False):
    case_id: str
    graph_version: str
    current_node: str
    facts: Annotated[dict[str, Any], merge_facts]
    path: Annotated[list[str], operator.add]


Handler = Callable[[CaseState, NodeSpec], dict[str, Any]]


class NodeFn(Protocol):
    def __call__(self, state: CaseState) -> dict[str, Any]: ...


class NoViableTransition(RuntimeError):
    """No outgoing edge condition holds. Phase 3 routes this to ESCALATE."""


def compile_graph(
    spec: GraphSpec,
    handlers: dict[str, Handler],
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[CaseState, None, CaseState, CaseState]:
    """`handlers` are looked up by node id first, then by node kind."""
    validate_graph(spec)
    builder: StateGraph[CaseState, None, CaseState, CaseState] = StateGraph(CaseState)

    for node in spec.nodes:
        handler = handlers.get(node.id) or handlers.get(node.kind)
        if handler is None:
            raise ValueError(f"no handler for node '{node.id}' (kind '{node.kind}')")
        builder.add_node(node.id, _wrap(handler, node))

    builder.add_edge(START, spec.start)
    for node in spec.nodes:
        if node.kind == "terminal":
            builder.add_edge(node.id, END)
            continue
        edges = spec.outgoing(node.id)
        if len(edges) == 1 and edges[0].condition is None:
            builder.add_edge(node.id, edges[0].target)
        else:
            builder.add_conditional_edges(
                node.id, _router(node.id, spec), [e.target for e in edges]
            )
    return builder.compile(checkpointer=checkpointer)


def _wrap(handler: Handler, node: NodeSpec) -> NodeFn:
    def run(state: CaseState) -> dict[str, Any]:
        update = handler(state, node)
        return {**update, "current_node": node.id, "path": [node.id]}

    run.__name__ = f"node_{node.id}"
    return run


def _router(node_id: str, spec: GraphSpec) -> Callable[[CaseState], str]:
    edges = spec.outgoing(node_id)

    def route(state: CaseState) -> str:
        data = dict(state)
        for edge in edges:
            if edge.condition is None or evaluate(edge.condition, data):
                return edge.target
        raise NoViableTransition(f"no viable transition from '{node_id}'")

    route.__name__ = f"route_{node_id}"
    return route
