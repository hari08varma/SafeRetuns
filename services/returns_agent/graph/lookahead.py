"""Look-ahead: prune options whose execution path cannot succeed, before they are offered.
Also renders a node's 2-hop neighbourhood for LLM guidance."""

from collections.abc import Callable
from typing import Any

from returns_agent.graph.schema import GraphSpec

Feasibility = dict[str, Callable[[dict[str, Any]], bool]]

# First two execution steps of each resolution (the look-ahead horizon).
OPTION_PATHS: dict[str, list[str]] = {
    "exchange": ["CREATE_EXCHANGE", "SCHEDULE_PICKUP"],
    "replacement": ["CREATE_REPLACEMENT", "SCHEDULE_PICKUP"],
    "refund": ["SCHEDULE_PICKUP"],
    "store_credit": ["SCHEDULE_PICKUP"],
    "keep_item_refund": ["KEEP_ITEM_REFUND"],
}


def failed_checks(
    spec: GraphSpec, node_id: str, state: dict[str, Any], feasibility: Feasibility
) -> list[str]:
    failed = []
    for check in spec.node(node_id).requires:
        fn = feasibility.get(check)
        if fn is None or not fn(state):
            failed.append(check)
    return failed


def feasible_options(
    spec: GraphSpec, options: list[str], state: dict[str, Any], feasibility: Feasibility
) -> tuple[list[str], dict[str, str]]:
    """Returns (feasible options in input order, {pruned option: reason})."""
    feasible: list[str] = []
    pruned: dict[str, str] = {}
    for option in options:
        reasons = [
            f"{node}:{check}"
            for node in OPTION_PATHS.get(option, [])
            for check in failed_checks(spec, node, state, feasibility)
        ]
        if reasons:
            pruned[option] = ", ".join(reasons)
        else:
            feasible.append(option)
    return feasible, pruned


def neighbourhood(spec: GraphSpec, node_id: str, hops: int = 2) -> dict[str, Any]:
    """Nodes within `hops` and the edges between them, with guidance and pitfalls."""
    frontier, seen = {node_id}, {node_id}
    for _ in range(hops):
        frontier = {e.target for n in frontier for e in spec.outgoing(n)} - seen
        seen |= frontier
    return {
        "nodes": [{"id": n.id, "task": n.task} for n in spec.nodes if n.id in seen],
        "edges": [
            {
                "from": e.source,
                "to": e.target,
                "condition": e.condition,
                "guidance": e.guidance,
                "pitfalls": e.pitfalls,
            }
            for e in spec.edges
            if e.source in seen and e.target in seen
        ],
    }
