"""Procedural graph as data: versioned JSON, validated before it can be compiled."""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeKind = Literal["code", "llm", "human", "terminal"]


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: NodeKind
    task: str = ""


class EdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    condition: dict[str, Any] | None = None  # JSONLogic; None = always
    priority: int = 0  # higher is evaluated first
    guidance: str = ""
    pitfalls: list[str] = Field(default_factory=list)


class GraphSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    start: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]

    def node(self, node_id: str) -> NodeSpec:
        return next(n for n in self.nodes if n.id == node_id)

    def outgoing(self, node_id: str) -> list[EdgeSpec]:
        return sorted((e for e in self.edges if e.source == node_id), key=lambda e: -e.priority)


class GraphValidationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def validate_graph(spec: GraphSpec) -> None:
    """Structural checks: known endpoints, terminals, reachability."""
    problems: list[str] = []
    ids = [n.id for n in spec.nodes]
    known = set(ids)
    if len(known) != len(ids):
        problems.append("duplicate node ids")
    if spec.start not in known:
        problems.append(f"start node '{spec.start}' does not exist")
    for e in spec.edges:
        for end in (e.source, e.target):
            if end not in known:
                problems.append(f"edge {e.source}->{e.target} references unknown node '{end}'")
    for n in spec.nodes:
        has_out = any(e.source == n.id for e in spec.edges)
        if n.kind == "terminal" and has_out:
            problems.append(f"terminal node '{n.id}' has outgoing edges")
        if n.kind != "terminal" and not has_out:
            problems.append(f"non-terminal node '{n.id}' has no outgoing edges")
    if problems:
        raise GraphValidationError(problems)

    # Every node must be reachable from start and must be able to reach a terminal.
    reachable = _closure(
        {spec.start}, {n.id: [e.target for e in spec.outgoing(n.id)] for n in spec.nodes}
    )
    for node_id in known - reachable:
        problems.append(f"node '{node_id}' is unreachable from start")
    reverse: dict[str, list[str]] = {n: [] for n in known}
    for e in spec.edges:
        reverse[e.target].append(e.source)
    can_finish = _closure({n.id for n in spec.nodes if n.kind == "terminal"}, reverse)
    for node_id in known - can_finish:
        problems.append(f"node '{node_id}' cannot reach a terminal node")
    if problems:
        raise GraphValidationError(problems)


def _closure(seeds: set[str], adjacency: dict[str, list[str]]) -> set[str]:
    seen, stack = set(seeds), list(seeds)
    while stack:
        for nxt in adjacency.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def load_graph(path: str | Path) -> GraphSpec:
    spec = GraphSpec.model_validate(json.loads(Path(path).read_text()))
    validate_graph(spec)
    return spec
