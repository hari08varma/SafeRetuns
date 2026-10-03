"""Graph returns-v1 structure, invariants, loop guards and look-ahead (no database)."""

from typing import Any

import pytest

from returns_agent.config import config_dir
from returns_agent.graph.compiler import choose_next
from returns_agent.graph.invariants import check_invariants
from returns_agent.graph.lookahead import feasible_options, neighbourhood
from returns_agent.graph.schema import GraphSpec, GraphValidationError, load_graph, validate_graph

SPEC = load_graph(config_dir() / "graphs" / "returns_v1.json")


def state(**facts: Any) -> dict[str, Any]:
    base = {"authenticated": True, "policy": {"eligible": True}}
    return {"facts": {**base, **facts}, "counters": {}}


def test_v1_has_the_25_planned_nodes() -> None:
    assert {n.id for n in SPEC.nodes} == {
        "START",
        "AUTHENTICATE",
        "IDENTIFY_ORDER",
        "UNDERSTAND_REQUEST",
        "CLARIFY",
        "CHECK_ELIGIBILITY",
        "EXPLAIN_INELIGIBLE",
        "REQUEST_EVIDENCE",
        "ASSESS_EVIDENCE",
        "RISK_SCORE",
        "GENERATE_OPTIONS",
        "SCORE_OPTIONS",
        "AUTONOMY_GATE",
        "HUMAN_APPROVAL",
        "CUSTOMER_CONFIRM",
        "SCHEDULE_PICKUP",
        "TRACK_SHIPMENT",
        "INSPECT_QC",
        "ISSUE_REFUND",
        "CREATE_REPLACEMENT",
        "CREATE_EXCHANGE",
        "KEEP_ITEM_REFUND",
        "DISPUTE",
        "ESCALATE",
        "CLOSE",
    }
    assert SPEC.fallback == "ESCALATE" and SPEC.enforce_invariants
    waits = {n.id for n in SPEC.nodes if n.waits_for}
    assert waits == {
        "CLARIFY",
        "REQUEST_EVIDENCE",
        "HUMAN_APPROVAL",
        "CUSTOMER_CONFIRM",
        "TRACK_SHIPMENT",
        "INSPECT_QC",
        "DISPUTE",
        "ESCALATE",
    }


def test_broken_graph_is_rejected() -> None:
    data = SPEC.model_dump(by_alias=True)
    data["fallback"] = "NOWHERE"
    data["nodes"][0]["on_failure"]["then"] = "GHOST"
    with pytest.raises(GraphValidationError) as err:
        validate_graph(GraphSpec.model_validate(data))
    assert any("NOWHERE" in p for p in err.value.problems)
    assert any("GHOST" in p for p in err.value.problems)


# --- Invariants: each one blocks the violating transition ------------------------------


@pytest.mark.parametrize(
    ("target", "facts", "expected"),
    [
        ("IDENTIFY_ORDER", {"authenticated": False}, ["INV-1"]),
        ("ESCALATE", {"authenticated": False}, []),  # escalation is always possible
        ("GENERATE_OPTIONS", {"policy": {"eligible": False}}, ["INV-2"]),
        (
            "ISSUE_REFUND",
            {
                "route": "approval",
                "approval": {"status": "approved"},
                "refund_quote": {"total_minor": 100, "max_refundable_minor": 100},
            },
            ["INV-3"],
        ),
        (
            "ISSUE_REFUND",
            {"refund_quote": {"total_minor": 101, "max_refundable_minor": 100}},
            ["INV-4"],
        ),
        ("KEEP_ITEM_REFUND", {}, ["INV-4"]),  # no quote at all
        (
            "ISSUE_REFUND",
            {"refund_issued": True, "refund_quote": {"total_minor": 1, "max_refundable_minor": 1}},
            ["INV-5"],
        ),
        ("CLOSE", {"close_outcome": "resolved"}, ["INV-7"]),
        ("CLOSE", {"close_outcome": "resolved", "execution_succeeded": True}, []),
        (
            "ISSUE_REFUND",
            {
                "route": "approval",
                "approval": {"status": "approved", "token": "t"},
                "refund_quote": {"total_minor": 5, "max_refundable_minor": 5},
            },
            [],
        ),
    ],
)
def test_invariants(target: str, facts: dict[str, Any], expected: list[str]) -> None:
    assert check_invariants(target, state(**facts)) == expected


def test_router_prunes_violating_edge_and_falls_back() -> None:
    s = state(
        qc={"passed": True},
        chosen_option="refund",
        route="approval",
        approval={"status": "approved"},
        refund_quote={"total_minor": 5, "max_refundable_minor": 5},
    )
    target, violations = choose_next(SPEC, SPEC.node("INSPECT_QC"), s)
    assert target == "ESCALATE"
    assert "INV-3:INSPECT_QC->ISSUE_REFUND" in violations


def test_loop_guards_escalate() -> None:
    s = state(missing_slots=["reason_category"])
    s["counters"] = {"clarifications": 3}
    assert choose_next(SPEC, SPEC.node("UNDERSTAND_REQUEST"), s) == (
        "ESCALATE",
        ["LOOP_GUARD:clarifications"],
    )
    s = state(missing_slots=[])
    s["counters"] = {"turns": 31}
    target, violations = choose_next(SPEC, SPEC.node("UNDERSTAND_REQUEST"), s)
    assert (target, violations) == ("ESCALATE", ["LOOP_GUARD:turns"])


def test_off_graph_request_escalates() -> None:
    s = state(missing_slots=[], requested_next="ISSUE_REFUND")
    assert choose_next(SPEC, SPEC.node("UNDERSTAND_REQUEST"), s) == (
        "ESCALATE",
        ["OFF_GRAPH:UNDERSTAND_REQUEST->ISSUE_REFUND"],
    )


def test_chooser_picks_among_viable_only() -> None:
    s = state(missing_slots=[])
    # Only one viable edge here, so the chooser is not consulted.
    assert (
        choose_next(SPEC, SPEC.node("UNDERSTAND_REQUEST"), s, chooser=lambda n, v, st: "CLARIFY")[0]
        == "CHECK_ELIGIBILITY"
    )


# --- Look-ahead -----------------------------------------------------------------------------


def test_lookahead_prunes_options_before_they_are_offered() -> None:
    feasibility = {"stock_available": lambda s: False, "pincode_serviceable": lambda s: True}
    feasible, pruned = feasible_options(
        SPEC, ["exchange", "refund", "keep_item_refund"], state(), feasibility
    )
    assert feasible == ["refund", "keep_item_refund"]
    assert pruned == {"exchange": "CREATE_EXCHANGE:stock_available"}


def test_lookahead_unserviceable_pincode_keeps_only_returnless() -> None:
    feasibility = {"stock_available": lambda s: True, "pincode_serviceable": lambda s: False}
    feasible, pruned = feasible_options(
        SPEC, ["exchange", "refund", "keep_item_refund"], state(), feasibility
    )
    assert feasible == ["keep_item_refund"]
    assert set(pruned) == {"exchange", "refund"}


def test_neighbourhood_two_hops() -> None:
    hood = neighbourhood(SPEC, "AUTONOMY_GATE", hops=2)
    ids = {n["id"] for n in hood["nodes"]}
    assert {
        "AUTONOMY_GATE",
        "CUSTOMER_CONFIRM",
        "HUMAN_APPROVAL",
        "ESCALATE",
        "CREATE_EXCHANGE",
        "CLOSE",
    } <= ids
    assert "TRACK_SHIPMENT" not in ids  # three hops away
    assert all(e["from"] in ids and e["to"] in ids for e in hood["edges"])
