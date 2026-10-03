"""End-to-end case runs on graph returns-v1 with a real Postgres checkpointer."""

import os
import shutil
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.adapters.mock import MockCarrierAdapter, MockInventoryAdapter
from returns_agent.audit import log as audit
from returns_agent.config import config_dir
from returns_agent.db.models import AuditEvent, Customer, Order, ReturnCase
from returns_agent.db.session import get_engine
from returns_agent.graph.events import InvalidEvent
from returns_agent.graph.nodes import build_handlers
from returns_agent.graph.registry import GraphRegistry
from returns_agent.graph.runner import CaseClosed, CaseRunner, Event, StaleEvent, make_feasibility
from tests.conftest import needs_db

pytestmark = [needs_db]


@pytest.fixture
def saver() -> Iterator[PostgresSaver]:
    with PostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as s:
        s.setup()
        yield s


@pytest.fixture
def inventory() -> MockInventoryAdapter:
    return MockInventoryAdapter({"KUR-L": 5})


@pytest.fixture
def carrier() -> MockCarrierAdapter:
    return MockCarrierAdapter(unserviceable={"999999"})


def make_runner(
    saver: PostgresSaver,
    inventory: MockInventoryAdapter,
    carrier: MockCarrierAdapter,
    directory: Path | None = None,
    active: str = "returns-v1",
) -> CaseRunner:
    registry = GraphRegistry(
        directory or config_dir() / "graphs",
        active,
        build_handlers,
        make_feasibility(inventory, carrier),
        saver,
    )
    return CaseRunner(registry, get_engine())


@pytest.fixture
def runner(
    saver: PostgresSaver, inventory: MockInventoryAdapter, carrier: MockCarrierAdapter
) -> CaseRunner:
    return make_runner(saver, inventory, carrier)


def new_case(db: Session, runner: CaseRunner) -> tuple[uuid.UUID, str]:
    customer = db.scalars(select(Customer)).first()
    assert customer is not None
    order = db.scalars(select(Order).where(Order.customer_id == customer.id)).first()
    assert order is not None
    case = ReturnCase(
        customer_id=customer.id, order_id=order.id, graph_version=runner.registry.active_version
    )
    db.add(case)
    db.commit()
    return case.id, str(customer.id)


def case_facts(customer_id: str, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    facts: dict[str, Any] = {
        "customer_id": customer_id,
        "principal_customer_id": customer_id,
        "pincode": "500081",
        "order": {
            "order_id": "ORD-1",
            "status": "delivered",
            "payment_method": "upi",
            "placed_at": (now - timedelta(days=20)).isoformat(),
            "delivered_at": (now - timedelta(days=5)).isoformat(),
        },
        "item": {"sku": "KUR-M", "category": "apparel", "qty_ordered": 1, "qty_returning": 1},
        "request": {"reason_category": "size_fit", "desired_resolution": "refund"},
        "pricing": {
            "unit_price_minor": 129900,
            "payments": [{"method": "upi", "amount_minor": 129900}],
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(facts.get(key), dict):
            facts[key] = {**facts[key], **value}
        else:
            facts[key] = value
    return facts


def nodes_audited(db: Session, case_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.case_id == case_id, AuditEvent.action == "graph.node")
        .order_by(AuditEvent.seq)
    ).all()
    return [r.payload["node"] for r in rows]


def test_refund_happy_path_end_to_end(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    r = runner.start(case_id, case_facts(cust))
    assert (r.current_node, r.waiting_for, r.status) == (
        "CUSTOMER_CONFIRM",
        "customer_confirm",
        "waiting",
    )
    assert r.facts["options"] == ["exchange", "store_credit", "refund"]
    assert r.facts["chosen_option"] == "refund"
    assert r.facts["refund_quote"] == {"total_minor": 129900, "max_refundable_minor": 129900}

    r = runner.dispatch(case_id, Event("customer_confirm", {"accept": True}, "customer", cust))
    assert r.current_node == "TRACK_SHIPMENT"
    r = runner.dispatch(case_id, Event("carrier_event", {"event": "picked_up"}))
    assert r.current_node == "TRACK_SHIPMENT"  # keeps waiting until received
    r = runner.dispatch(case_id, Event("carrier_event", {"event": "received"}))
    assert r.current_node == "INSPECT_QC"
    r = runner.dispatch(
        case_id, Event("qc_result", {"passed": True, "grade": "A"}, "staff", "qc-1")
    )
    assert (r.status, r.current_node, r.waiting_for) == ("closed", "CLOSE", None)
    assert r.facts["close_outcome"] == "resolved" and r.facts["refund_issued"] is True

    seeded_db.expire_all()
    case = seeded_db.get(ReturnCase, case_id)
    assert case is not None and case.status == "closed" and case.current_node == "CLOSE"
    assert nodes_audited(seeded_db, case_id)[-3:] == ["INSPECT_QC", "ISSUE_REFUND", "CLOSE"]
    assert audit.verify(seeded_db, case_id).ok
    with pytest.raises(CaseClosed):
        runner.dispatch(case_id, Event("qc_result", {"passed": True}))


def test_ineligible_case_closes_with_explanation(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    r = runner.start(case_id, case_facts(cust, item={"category": "innerwear"}))
    assert r.status == "closed" and r.facts["close_outcome"] == "rejected"
    assert r.facts["explanation"] == ["NON_RETURNABLE_CATEGORY"]


def test_unauthenticated_case_escalates(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    r = runner.start(case_id, case_facts(cust, principal_customer_id=str(uuid.uuid4())))
    assert r.status == "escalated" and r.current_node == "ESCALATE"
    assert "NO_VIABLE_TRANSITION:AUTHENTICATE" in r.violations


@pytest.mark.parametrize(
    ("decision", "expected_node", "expected_option"),
    [
        (
            {"decision": "approve", "approver_id": "a1", "token": "tok"},
            "CUSTOMER_CONFIRM",
            "refund",
        ),
        (
            {"decision": "modify", "approver_id": "a1", "token": "tok", "option": "store_credit"},
            "CUSTOMER_CONFIRM",
            "store_credit",
        ),
        (
            {"decision": "reject", "approver_id": "a1", "reason_code": "SUSPECTED_ABUSE"},
            "CLOSE",
            "refund",
        ),
    ],
)
def test_approval_pause_and_resume(
    seeded_db: Session,
    runner: CaseRunner,
    decision: dict[str, Any],
    expected_node: str,
    expected_option: str,
) -> None:
    case_id, cust = new_case(seeded_db, runner)
    r = runner.start(case_id, case_facts(cust, route_override="approval"))
    assert (r.current_node, r.waiting_for) == ("HUMAN_APPROVAL", "approval")
    r = runner.dispatch(case_id, Event("approval", decision, "staff", "a1"))
    assert r.current_node == expected_node
    assert r.facts["chosen_option"] == expected_option
    assert nodes_audited(seeded_db, case_id).count("HUMAN_APPROVAL") == 1


def test_approval_without_token_cannot_refund(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    runner.start(case_id, case_facts(cust, route_override="approval"))
    runner.dispatch(case_id, Event("approval", {"decision": "approve", "approver_id": "a1"}))
    runner.dispatch(case_id, Event("customer_confirm", {"accept": True}))
    runner.dispatch(case_id, Event("carrier_event", {"event": "received"}))
    r = runner.dispatch(case_id, Event("qc_result", {"passed": True}))
    assert r.status == "escalated"
    assert "INV-3:INSPECT_QC->ISSUE_REFUND" in r.violations
    assert not r.facts.get("refund_issued")


def test_invalid_stale_and_wrong_events_are_rejected(
    seeded_db: Session, runner: CaseRunner
) -> None:
    case_id, cust = new_case(seeded_db, runner)
    runner.start(case_id, case_facts(cust, route_override="approval"))
    with pytest.raises(StaleEvent):
        runner.dispatch(case_id, Event("customer_confirm", {"accept": True}))
    with pytest.raises(InvalidEvent):
        runner.dispatch(case_id, Event("approval", {"decision": "approvee", "approver_id": "a"}))
    seeded_db.expire_all()
    case = seeded_db.get(ReturnCase, case_id)
    assert case is not None and case.current_node == "HUMAN_APPROVAL"  # untouched


def test_concurrent_duplicate_events_apply_once(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    runner.start(case_id, case_facts(cust, route_override="approval"))
    outcomes: list[str] = []

    def approve() -> None:
        try:
            runner.dispatch(
                case_id,
                Event("approval", {"decision": "approve", "approver_id": "a1", "token": "t"}),
            )
            outcomes.append("ok")
        except StaleEvent:
            outcomes.append("stale")

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok", "stale"]
    assert nodes_audited(seeded_db, case_id).count("HUMAN_APPROVAL") == 1


def test_restart_resumes_from_checkpoint_without_duplicates(
    seeded_db: Session,
    saver: PostgresSaver,
    inventory: MockInventoryAdapter,
    carrier: MockCarrierAdapter,
) -> None:
    first = make_runner(saver, inventory, carrier)
    case_id, cust = new_case(seeded_db, first)
    first.start(case_id, case_facts(cust))
    first.dispatch(case_id, Event("customer_confirm", {"accept": True}))
    del first
    # A new process: fresh checkpointer connection, registry and runner.
    with PostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as fresh_saver:
        second = make_runner(fresh_saver, inventory, carrier)
        second.dispatch(case_id, Event("carrier_event", {"event": "received"}))
        r = second.dispatch(case_id, Event("qc_result", {"passed": True}))
    assert r.status == "closed" and r.facts["refund_issued"] is True
    assert nodes_audited(seeded_db, case_id).count("ISSUE_REFUND") == 1


def test_lookahead_prunes_out_of_stock_exchange(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    r = runner.start(
        case_id,
        case_facts(cust, request={"desired_resolution": "exchange", "exchange_sku": "KUR-XL"}),
    )
    assert "exchange" not in r.facts["options"]
    assert r.facts["pruned_options"] == {"exchange": "CREATE_EXCHANGE:stock_available"}


def test_unserviceable_pincode_escalates(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    r = runner.start(case_id, case_facts(cust, pincode="999999"))
    assert r.facts["options"] == [] and r.status == "escalated"


def test_clarification_loop_guard(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    r = runner.start(case_id, case_facts(cust, request={"reason_category": None}))
    assert r.current_node == "CLARIFY" and r.facts["pending_prompt"]
    for _ in range(2):
        r = runner.dispatch(case_id, Event("customer_message", {"text": "hmm"}))
        assert r.current_node == "CLARIFY"
    r = runner.dispatch(case_id, Event("customer_message", {"text": "still unclear"}))
    assert r.status == "escalated" and "LOOP_GUARD:clarifications" in r.violations


def test_clarification_answer_continues(seeded_db: Session, runner: CaseRunner) -> None:
    case_id, cust = new_case(seeded_db, runner)
    runner.start(case_id, case_facts(cust, request={"reason_category": None}))
    r = runner.dispatch(
        case_id, Event("customer_message", {"answers": {"reason_category": "size_fit"}})
    )
    assert r.current_node == "CUSTOMER_CONFIRM"


def test_case_finishes_on_the_version_it_started(
    seeded_db: Session,
    saver: PostgresSaver,
    inventory: MockInventoryAdapter,
    carrier: MockCarrierAdapter,
    tmp_path: Path,
) -> None:
    shutil.copy(config_dir() / "graphs" / "returns_v1.json", tmp_path / "returns_v1.json")
    v2 = (tmp_path / "returns_v1.json").read_text().replace('"returns-v1"', '"returns-v2"')
    (tmp_path / "returns_v2.json").write_text(v2)

    old_runner = make_runner(saver, inventory, carrier, tmp_path, active="returns-v1")
    old_case, cust = new_case(seeded_db, old_runner)
    old_runner.start(old_case, case_facts(cust, route_override="approval"))

    new_runner = make_runner(saver, inventory, carrier, tmp_path, active="returns-v2")
    new_case_id, _ = new_case(seeded_db, new_runner)
    new_runner.start(new_case_id, case_facts(cust))

    new_runner.dispatch(old_case, Event("approval", {"decision": "reject", "approver_id": "a"}))
    rows = seeded_db.scalars(select(AuditEvent).where(AuditEvent.action == "graph.node")).all()
    versions = {(r.case_id, r.payload["graph_version"]) for r in rows}
    assert {v for c, v in versions if c == old_case} == {"returns-v1"}
    assert {v for c, v in versions if c == new_case_id} == {"returns-v2"}
