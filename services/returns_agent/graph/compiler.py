"""Compile a validated GraphSpec (JSON) into an executable LangGraph StateGraph.

The JSON graph is the source of truth; LangGraph provides execution, checkpointing
and pause/resume. Each node wrapper runs the handler (retrying per its contract), then
chooses the next node itself: edge conditions → loop guards → hard invariants →
look-ahead feasibility → (optional) decision chooser. Every rejected path is recorded
in `violations`; when nothing is viable the case goes to the graph's fallback.
"""

import logging
import operator
from collections.abc import Callable
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from returns_agent.graph.conditions import evaluate
from returns_agent.graph.invariants import check_invariants, loop_guard_exceeded
from returns_agent.graph.lookahead import Feasibility, failed_checks
from returns_agent.graph.schema import GraphSpec, NodeSpec, validate_graph

logger = logging.getLogger(__name__)


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class CaseState(TypedDict, total=False):
    case_id: str
    graph_version: str
    current_node: str
    next_node: str | None
    facts: Annotated[dict[str, Any], merge_dicts]
    counters: Annotated[dict[str, int], merge_dicts]
    path: Annotated[list[str], operator.add]
    violations: Annotated[list[str], operator.add]
    last_event: dict[str, Any] | None


Handler = Callable[[CaseState, NodeSpec], dict[str, Any]]
Chooser = Callable[[NodeSpec, list[str], dict[str, Any]], str]


class NodeFn(Protocol):
    def __call__(self, state: CaseState) -> dict[str, Any]: ...


class NoViableTransition(RuntimeError):
    """No outgoing edge is viable and the graph has no fallback."""


def choose_next(
    spec: GraphSpec,
    node: NodeSpec,
    state: dict[str, Any],
    feasibility: Feasibility | None = None,
    chooser: Chooser | None = None,
) -> tuple[str, list[str]]:
    """Returns (next node id, violations recorded while deciding)."""
    feasibility = feasibility or {}
    violations: list[str] = []
    fallback = spec.fallback if spec.fallback != node.id else None

    outgoing = spec.outgoing(node.id)
    requested = (state.get("facts") or {}).get("requested_next")
    if requested and requested not in {e.target for e in outgoing}:
        violations.append(f"OFF_GRAPH:{node.id}->{requested}")
        if fallback:
            return fallback, violations
        raise NoViableTransition(f"off-graph transition {node.id}->{requested}")

    viable: list[str] = []
    for edge in outgoing:
        if requested and edge.target != requested:
            continue
        if edge.condition is not None and not evaluate(edge.condition, state):
            continue
        if spec.enforce_invariants:
            guard = loop_guard_exceeded(edge.target, state)
            if guard and fallback:
                return fallback, [*violations, f"LOOP_GUARD:{guard}"]
            broken = check_invariants(edge.target, state)
            if broken:
                violations += [f"{inv}:{node.id}->{edge.target}" for inv in broken]
                continue
        infeasible = failed_checks(spec, edge.target, state, feasibility)
        if infeasible:
            violations += [f"INFEASIBLE:{edge.target}:{c}" for c in infeasible]
            continue
        viable.append(edge.target)

    if not viable:
        if fallback:
            return fallback, [*violations, f"NO_VIABLE_TRANSITION:{node.id}"]
        raise NoViableTransition(f"no viable transition from '{node.id}'")
    target = chooser(node, viable, state) if chooser and len(viable) > 1 else viable[0]
    if target not in viable:
        raise NoViableTransition(f"chooser picked non-viable '{target}' from '{node.id}'")
    return target, violations


def compile_graph(
    spec: GraphSpec,
    handlers: dict[str, Handler],
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    feasibility: Feasibility | None = None,
    chooser: Chooser | None = None,
) -> CompiledStateGraph[CaseState, None, CaseState, CaseState]:
    """`handlers` are looked up by node id first, then by node kind."""
    validate_graph(spec)
    builder: StateGraph[CaseState, None, CaseState, CaseState] = StateGraph(CaseState)

    for node in spec.nodes:
        handler = handlers.get(node.id) or handlers.get(node.kind)
        if handler is None:
            raise ValueError(f"no handler for node '{node.id}' (kind '{node.kind}')")
        builder.add_node(node.id, _wrap(spec, node, handler, feasibility, chooser))

    builder.add_edge(START, spec.start)
    for node in spec.nodes:
        if node.kind == "terminal":
            builder.add_edge(node.id, END)
            continue
        targets = {e.target for e in spec.outgoing(node.id)}
        targets |= {t for t in (spec.fallback, node.on_failure.then) if t and t != node.id}
        builder.add_conditional_edges(node.id, _read_next, sorted(targets))
    return builder.compile(checkpointer=checkpointer)


def _read_next(state: CaseState) -> str:
    target = state.get("next_node")
    if not target:
        raise NoViableTransition("node did not choose a next node")
    return target


def _wrap(
    spec: GraphSpec,
    node: NodeSpec,
    handler: Handler,
    feasibility: Feasibility | None,
    chooser: Chooser | None,
) -> NodeFn:
    def run(state: CaseState) -> dict[str, Any]:
        event: dict[str, Any] | None = None
        if node.waits_for:
            # Pause until the expected event arrives; the node re-runs from here on resume.
            event = interrupt(
                {
                    "case_id": state.get("case_id"),
                    "node": node.id,
                    "waiting_for": node.waits_for,
                    "prompt": (state.get("facts") or {}).get("pending_prompt"),
                }
            )
        view: CaseState = {**state, "last_event": event}

        update: dict[str, Any] | None = None
        error: Exception | None = None
        for _ in range(node.on_failure.retry + 1):
            try:
                update = handler(view, node)
                break
            except GraphBubbleUp:
                raise  # interrupts raised inside handlers must reach LangGraph
            except Exception as exc:  # contract: retry, then route to the failure target
                error = exc
                logger.warning("node %s failed: %s", node.id, exc)

        base = {"current_node": node.id, "path": [node.id], "last_event": event}
        if update is None:
            target = node.on_failure.then or spec.fallback
            if target is None:
                assert error is not None
                raise error
            failure = f"NODE_FAILED:{node.id}:{type(error).__name__}"
            return {**base, "next_node": target, "violations": [failure]}
        if node.kind == "terminal":
            return {**update, **base, "next_node": None}
        merged = {
            **state,
            **update,
            "facts": merge_dicts(state.get("facts"), update.get("facts")),
            "counters": merge_dicts(state.get("counters"), update.get("counters")),
        }
        target, violations = choose_next(spec, node, merged, feasibility, chooser)
        return {**update, **base, "next_node": target, "violations": violations}

    run.__name__ = f"node_{node.id}"
    return run
