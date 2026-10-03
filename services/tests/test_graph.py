from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from returns_agent.graph.compiler import CaseState, Handler, NoViableTransition, compile_graph
from returns_agent.graph.schema import GraphSpec, GraphValidationError, NodeSpec, load_graph

GRAPHS = Path(__file__).resolve().parents[2] / "config" / "graphs"


def handlers() -> dict[str, Handler]:
    def noop(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {}

    def eligibility(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"eligible": state["facts"]["days_since_delivery"] <= 30}}

    def human(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"approved": interrupt({"ask": "approve?"})["approve"]}}

    return {
        "llm": noop,
        "code": noop,
        "terminal": noop,
        "check_eligibility": eligibility,
        "human": human,
    }


def run_case(days: int, approve: bool | None = None) -> tuple[dict[str, Any], tuple[str, ...]]:
    graph = compile_graph(load_graph(GRAPHS / "spike_v1.json"), handlers(), InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    result: dict[str, Any] = graph.invoke(
        {"case_id": "t1", "facts": {"days_since_delivery": days}}, config
    )  # type: ignore[arg-type]
    if approve is not None:
        result = graph.invoke(Command(resume={"approve": approve}), config)  # type: ignore[arg-type]
    return result, tuple(graph.get_state(config).next)  # type: ignore[arg-type]


def test_ineligible_case_is_rejected_without_human() -> None:
    result, waiting = run_case(days=45)
    assert result["current_node"] == "close_rejected"
    assert result["path"] == ["intake", "check_eligibility", "close_rejected"]
    assert waiting == ()


def test_eligible_case_pauses_for_approval() -> None:
    _, waiting = run_case(days=5)
    assert waiting == ("human_approval",)


def test_approve_and_reject_paths() -> None:
    approved, _ = run_case(days=5, approve=True)
    rejected, _ = run_case(days=5, approve=False)
    assert approved["current_node"] == "close_resolved"
    assert "issue_refund" in approved["path"]
    assert rejected["current_node"] == "close_rejected"
    assert "issue_refund" not in rejected["path"]


def test_facts_are_merged_not_replaced() -> None:
    result, _ = run_case(days=5, approve=True)
    assert result["facts"] == {"days_since_delivery": 5, "eligible": True, "approved": True}


def test_no_viable_transition_raises() -> None:
    spec = GraphSpec.model_validate(
        {
            "version": "t",
            "start": "a",
            "nodes": [{"id": "a", "kind": "code"}, {"id": "end", "kind": "terminal"}],
            "edges": [{"from": "a", "to": "end", "condition": {"==": [1, 2]}}],
        }
    )
    graph = compile_graph(spec, handlers())
    with pytest.raises(NoViableTransition):
        graph.invoke({"case_id": "x", "facts": {}})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("graph", "problem"),
    [
        (
            {"start": "zz", "nodes": [{"id": "end", "kind": "terminal"}], "edges": []},
            "start node 'zz' does not exist",
        ),
        (
            {"start": "a", "nodes": [{"id": "a", "kind": "code"}], "edges": []},
            "non-terminal node 'a' has no outgoing edges",
        ),
        (
            {
                "start": "a",
                "nodes": [
                    {"id": "a", "kind": "code"},
                    {"id": "end", "kind": "terminal"},
                    {"id": "orphan", "kind": "terminal"},
                ],
                "edges": [{"from": "a", "to": "end"}],
            },
            "node 'orphan' is unreachable from start",
        ),
        (
            {
                "start": "a",
                "nodes": [
                    {"id": "a", "kind": "code"},
                    {"id": "b", "kind": "code"},
                    {"id": "end", "kind": "terminal"},
                ],
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "b"},
                    {"from": "a", "to": "end"},
                ],
            },
            "node 'b' cannot reach a terminal node",
        ),
        (
            {
                "start": "a",
                "nodes": [{"id": "a", "kind": "code"}, {"id": "end", "kind": "terminal"}],
                "edges": [{"from": "a", "to": "end"}, {"from": "a", "to": "ghost"}],
            },
            "references unknown node 'ghost'",
        ),
    ],
)
def test_structural_validation(graph: dict[str, Any], problem: str) -> None:
    with pytest.raises(GraphValidationError) as err:
        load_spec = GraphSpec.model_validate({"version": "t", **graph})
        compile_graph(load_spec, handlers())
    assert any(problem in p for p in err.value.problems)


def test_spike_graphs_are_valid() -> None:
    for name in ("spike_v1.json", "spike_v2.json"):
        load_graph(GRAPHS / name)
