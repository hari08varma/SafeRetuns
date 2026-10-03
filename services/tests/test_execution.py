"""Phase 6: the five resolution flows end to end through the API, plus sagas, retries,
idempotency, webhook security, timers/SLAs and reconciliation."""

import json
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.adapters.bundle import Adapters
from returns_agent.adapters.mock import (
    FaultPlan,
    MockCarrierAdapter,
    MockInventoryAdapter,
    MockNotificationAdapter,
    MockOrderAdapter,
    MockPaymentAdapter,
)
from returns_agent.agent.cases import CaseService
from returns_agent.api.app import create_app
from returns_agent.config import config_dir
from returns_agent.db.models import (
    AuditEvent,
    Customer,
    Exchange,
    Notification,
    Order,
    OrderItem,
    Outbox,
    Refund,
    ReplacementOrder,
    ReturnCase,
    Shipment,
    SlaTimer,
)
from returns_agent.db.session import get_engine
from returns_agent.execution.relay import Relay
from returns_agent.execution.webhooks import sign
from returns_agent.graph.nodes import build_handlers
from returns_agent.graph.registry import GraphRegistry
from returns_agent.graph.runner import CaseRunner, make_feasibility
from returns_agent.lifecycle import timers
from returns_agent.security.tokens import Principal, Role, issue_token
from returns_agent.seed.generator import generate
from tests.conftest import needs_db

pytestmark = [needs_db]
SECRET = os.environ["WEBHOOK_SECRET"]


@dataclass
class Env:
    client: TestClient
    relay: Relay
    runner: CaseRunner
    adapters: Adapters
    faults: FaultPlan
    db: Session
    customer: Customer

    def customer_headers(self) -> dict[str, str]:
        return auth(Principal(str(self.customer.id), Role.CUSTOMER))

    def open(self, sku: str, price: int, **request: str) -> dict[str, Any]:
        ref = make_order(self.db, self.customer, sku, price)
        body = {
            "order_id": ref,
            "item_id": f"{ref}-1",
            "message": "I'd like to return this",
            **request,
        }
        r = self.client.post("/api/v1/cases", json=body, headers=self.customer_headers())
        assert r.status_code == 201, r.text
        view: dict[str, Any] = r.json()
        return view

    def confirm(self, case_id: object, **extra: object) -> dict[str, Any]:
        r = self.client.post(
            f"/api/v1/cases/{case_id}/confirm",
            json={"accept": True, **extra},
            headers=self.customer_headers(),
        )
        assert r.status_code == 200, r.text
        view: dict[str, Any] = r.json()
        return view

    def carrier(self, case_id: object, event: str, event_id: str | None = None) -> str:
        shipment = self.db.scalars(select(Shipment).where(Shipment.case_id == case_id)).one()
        payload = {
            "event_id": event_id or f"{case_id}-{event}-{time.time_ns()}",
            "awb": shipment.awb,
            "event": event,
        }
        r = signed_post(self.client, "/api/v1/webhooks/carrier", payload)
        assert r.status_code == 200, r.text
        return str(r.json()["result"])

    def qc(self, case_id: object, passed: bool = True) -> dict[str, Any]:
        r = self.client.post(
            f"/api/v1/console/cases/{case_id}/qc",
            json={"passed": passed, "grade": "A"},
            headers=auth(Principal("qc-staff", Role.QC_OPERATOR)),
        )
        assert r.status_code == 200, r.text
        result: dict[str, Any] = r.json()
        return result

    def case(self, case_id: object) -> ReturnCase:
        self.db.expire_all()
        case = self.db.get(ReturnCase, case_id)
        assert case is not None
        return case


def auth(principal: Principal) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(principal, 'access')}"}


def signed_post(
    client: TestClient,
    path: str,
    payload: Mapping[str, object],
    secret: str = SECRET,
    ts: int | None = None,
) -> Any:
    body = json.dumps(payload).encode()
    stamp = str(ts if ts is not None else int(time.time()))
    return client.post(
        path,
        content=body,
        headers={
            "content-type": "application/json",
            "x-timestamp": stamp,
            "x-signature": sign(secret, stamp, body),
        },
    )


_orders = iter(range(1, 10_000))


def make_order(db: Session, customer: Customer, sku: str, price: int) -> str:
    ref = f"ORD-E{next(_orders):04d}"
    now = datetime.now(UTC)
    order = Order(
        external_id=ref,
        customer_id=customer.id,
        placed_at=now - timedelta(days=8),
        delivered_at=now - timedelta(days=5),
        status="delivered",
        payment_method="upi",
        coupon_minor=0,
        total_minor=price,
    )
    order.items = [
        OrderItem(
            external_id=f"{ref}-1",
            sku=sku,
            qty=1,
            unit_price_minor=price,
            discount_alloc_minor=0,
            final_sale=False,
        )
    ]
    db.add(order)
    db.commit()
    return ref


@pytest.fixture
def env(seeded_db: Session) -> Iterator[Env]:
    faults = FaultPlan(sleep=lambda _: None)
    adapters = Adapters(
        orders=MockOrderAdapter(generate(seed=1, customers=1), faults),
        carrier=MockCarrierAdapter(faults=faults),
        payment=MockPaymentAdapter(faults),
        inventory=MockInventoryAdapter({"KUR-L": 2, "KUR-M": 2}, faults),
        notification=MockNotificationAdapter(),
    )
    with PostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as saver:
        saver.setup()
        registry = GraphRegistry(
            config_dir() / "graphs",
            "returns-v1",
            partial(build_handlers, llm=None),
            make_feasibility(adapters.inventory, adapters.carrier),
            saver,
        )
        runner = CaseRunner(registry, get_engine())
        client = TestClient(create_app(adapters, cases=CaseService(runner, None)))
        customer = seeded_db.scalars(select(Customer)).first()
        assert customer is not None
        yield Env(
            client,
            Relay(get_engine(), runner, adapters),
            runner,
            adapters,
            faults,
            seeded_db,
            customer,
        )


def sent_templates(env: Env, case_id: object, audience: str = "customer") -> set[str]:
    env.db.expire_all()
    rows = env.db.scalars(
        select(Notification).where(
            Notification.case_id == case_id,
            Notification.audience == audience,
            Notification.status == "sent",
        )
    ).all()
    return {n.template for n in rows}


# --- The five flows ---


def test_refund_flow(env: Env) -> None:
    view = env.open("KUR-M", 129900, reason_category="size_fit", desired_resolution="refund")
    case_id = view["case_id"]
    assert view["waiting_for"] == "customer_confirm" and view["refund_total_minor"] == 129900
    assert env.confirm(case_id)["current_node"] == "SCHEDULE_PICKUP"
    env.relay.drain()
    assert env.case(case_id).current_node == "TRACK_SHIPMENT"
    assert env.carrier(case_id, "picked_up") == "applied"
    assert env.carrier(case_id, "received") == "applied"
    assert env.qc(case_id)["current_node"] == "ISSUE_REFUND"
    env.relay.drain()

    case = env.case(case_id)
    assert (case.status, case.current_node) == ("closed", "CLOSE")
    refund = env.db.scalars(select(Refund).where(Refund.case_id == case_id)).one()
    assert (refund.amount_minor, refund.status, refund.method) == (129900, "succeeded", "source")
    assert env.db.scalars(select(Shipment).where(Shipment.case_id == case_id)).one().status == (
        "received"
    )
    assert {"pickup_scheduled", "item_received", "refund_issued", "case_closed"} <= sent_templates(
        env, case_id
    )
    timeline = env.client.get(
        f"/api/v1/cases/{case_id}/timeline", headers=env.customer_headers()
    ).json()
    labels = [e["label"] for e in timeline if e["type"] == "milestone"]
    assert labels[-3:] == ["Quality check done", "Refund processed", "Case closed"]
    assert all(e.get("audience") != "staff" for e in timeline)
    staff = env.client.get(
        f"/api/v1/console/cases/{case_id}/timeline", headers=auth(Principal("agent-1", Role.AGENT))
    ).json()
    assert any(e["type"] == "decision" for e in staff)
    assert any(e["type"] == "audit" and e["action"] == "outbox.done" for e in staff)


def test_keep_item_flow(env: Env) -> None:
    view = env.open(
        "SRM-30ml", 79900, reason_category="not_as_described", desired_resolution="refund"
    )
    case_id = view["case_id"]
    assert "keep_item_refund" in view["options"]
    assert env.confirm(case_id)["current_node"] == "KEEP_ITEM_REFUND"
    env.relay.drain()
    assert env.case(case_id).status == "closed"
    assert env.db.scalars(select(Refund).where(Refund.case_id == case_id)).one().amount_minor == (
        79900
    )
    assert env.db.scalars(select(Shipment).where(Shipment.case_id == case_id)).first() is None


def test_replacement_flow(env: Env) -> None:
    view = env.open(
        "KUR-M", 129900, reason_category="not_as_described", desired_resolution="replacement"
    )
    case_id = view["case_id"]
    assert env.confirm(case_id)["current_node"] == "CREATE_REPLACEMENT"
    env.relay.drain()  # replacement order, then pickup
    assert env.case(case_id).current_node == "TRACK_SHIPMENT"
    env.carrier(case_id, "received")
    assert env.qc(case_id)["status"] == "closed"
    repl = env.db.scalars(select(ReplacementOrder).where(ReplacementOrder.case_id == case_id)).one()
    assert repl.status == "created" and repl.new_order_ref
    assert env.db.scalars(select(Refund).where(Refund.case_id == case_id)).first() is None


def test_exchange_flow(env: Env) -> None:
    view = env.open("KUR-M", 129900, reason_category="size_fit", desired_resolution="exchange")
    case_id = view["case_id"]
    assert env.confirm(case_id, exchange_sku="KUR-L")["current_node"] == "CREATE_EXCHANGE"
    env.relay.drain()
    exchange = env.db.scalars(select(Exchange).where(Exchange.case_id == case_id)).one()
    assert (exchange.from_sku, exchange.to_sku, exchange.status) == ("KUR-M", "KUR-L", "created")
    assert isinstance(env.adapters.inventory, MockInventoryAdapter)
    assert env.adapters.inventory.stock["KUR-L"] == 1  # one unit reserved
    env.carrier(case_id, "received")
    assert env.qc(case_id)["status"] == "closed"


def test_rejection_flow(env: Env) -> None:
    view = env.open("INR-M", 69900, reason_category="size_fit", desired_resolution="refund")
    assert view["status"] == "closed" and "unable to accept" in str(view["reply"])
    actions = env.db.scalars(select(Outbox.action).where(Outbox.case_id == view["case_id"])).all()
    assert set(actions) == {"send_notification"}  # nothing to execute


# --- Saga, retries, idempotency ---


def test_three_failed_pickups_compensate_and_escalate(env: Env) -> None:
    view = env.open(
        "KUR-M", 129900, reason_category="not_as_described", desired_resolution="replacement"
    )
    case_id = view["case_id"]
    env.confirm(case_id)
    env.relay.drain()
    for _ in range(3):
        env.carrier(case_id, "pickup_failed")
    assert env.case(case_id).status == "escalated"
    env.relay.drain()  # runs the compensation
    repl = env.db.scalars(select(ReplacementOrder).where(ReplacementOrder.case_id == case_id)).one()
    assert repl.status == "cancelled"
    assert isinstance(env.adapters.orders, MockOrderAdapter)
    assert list(env.adapters.orders.cancelled.values()) == [repl.new_order_ref]
    assert "pickup_failed_dropoff_available" in sent_templates(env, case_id)
    assert "case_escalated" in sent_templates(env, case_id, audience="staff")


def test_refund_retries_with_backoff_then_succeeds(env: Env) -> None:
    view = env.open(
        "SRM-30ml", 79900, reason_category="not_as_described", desired_resolution="refund"
    )
    case_id = view["case_id"]
    env.faults.inject("payment.refund", "timeout", "error")
    env.confirm(case_id)
    t0 = datetime.now(UTC)
    env.relay.drain(t0)  # attempt 1 fails -> retry in 30s
    assert env.case(case_id).current_node == "KEEP_ITEM_REFUND"
    env.relay.drain(t0 + timedelta(seconds=10))  # not due yet: nothing happens
    assert env.faults.calls["payment.refund"] == 1
    env.relay.drain(t0 + timedelta(seconds=31))  # attempt 2 fails -> retry in 60s
    env.relay.drain(t0 + timedelta(seconds=100))  # attempt 3 succeeds
    assert env.case(case_id).status == "closed"
    assert len(env.db.scalars(select(Refund).where(Refund.case_id == case_id)).all()) == 1
    failures = env.db.scalars(
        select(AuditEvent).where(
            AuditEvent.case_id == case_id, AuditEvent.action == "outbox.attempt_failed"
        )
    ).all()
    assert len(failures) == 2


def test_permanent_failure_escalates(env: Env) -> None:
    env.relay.max_attempts = 2
    view = env.open(
        "SRM-30ml", 79900, reason_category="not_as_described", desired_resolution="refund"
    )
    case_id = view["case_id"]
    env.faults.inject("payment.refund", "error", "error")
    env.confirm(case_id)
    t0 = datetime.now(UTC)
    env.relay.drain(t0)
    env.relay.drain(t0 + timedelta(minutes=5))
    case = env.case(case_id)
    assert case.status == "escalated"
    assert env.db.scalars(select(Refund).where(Refund.case_id == case_id)).first() is None


def test_repeating_an_action_has_one_business_effect(env: Env) -> None:
    view = env.open(
        "SRM-30ml", 79900, reason_category="not_as_described", desired_resolution="refund"
    )
    case_id = view["case_id"]
    env.confirm(case_id)
    env.relay.drain()
    assert env.case(case_id).status == "closed"
    # Simulate crashes/replays: put every executed action back in the queue, three times.
    for _ in range(3):
        for row in env.db.scalars(select(Outbox).where(Outbox.case_id == case_id)):
            row.status = "pending"
        env.db.commit()
        env.relay.drain()
    assert isinstance(env.adapters.payment, MockPaymentAdapter)
    assert len(env.adapters.payment.refunds) == 1
    assert len(env.db.scalars(select(Refund).where(Refund.case_id == case_id)).all()) == 1
    assert env.case(case_id).status == "closed"


def test_reconcile_requeues_a_lost_action(env: Env) -> None:
    view = env.open("KUR-M", 129900, reason_category="size_fit", desired_resolution="refund")
    case_id = view["case_id"]
    env.confirm(case_id)
    env.db.execute(
        delete(Outbox).where(Outbox.case_id == case_id, Outbox.action == "schedule_pickup")
    )
    env.db.commit()  # as if the process died before the outbox commit
    env.relay.drain()
    assert env.case(case_id).current_node == "SCHEDULE_PICKUP"  # stuck
    assert env.runner.reconcile(case_id) is True
    env.relay.drain()
    assert env.case(case_id).current_node == "TRACK_SHIPMENT"


# --- Webhook security ---


def test_webhook_signature_timestamp_and_duplicates(env: Env) -> None:
    view = env.open("KUR-M", 129900, reason_category="size_fit", desired_resolution="refund")
    case_id = view["case_id"]
    env.confirm(case_id)
    env.relay.drain()
    shipment = env.db.scalars(select(Shipment).where(Shipment.case_id == case_id)).one()
    payload = {"event_id": "evt-1", "awb": shipment.awb, "event": "picked_up"}
    assert (
        signed_post(env.client, "/api/v1/webhooks/carrier", payload, secret="wrong").status_code
        == 401
    )
    assert (
        signed_post(
            env.client, "/api/v1/webhooks/carrier", payload, ts=int(time.time()) - 3600
        ).status_code
        == 401
    )
    unsigned = env.client.post("/api/v1/webhooks/carrier", json=payload)
    assert unsigned.status_code == 401
    assert env.carrier(case_id, "picked_up", event_id="evt-1") == "applied"
    assert env.carrier(case_id, "picked_up", event_id="evt-1") == "duplicate"
    env.db.expire_all()
    assert env.db.get(Shipment, shipment.id).events.count("picked_up") == 1  # type: ignore[union-attr]


def test_payment_webhook_failure_alerts_staff(env: Env) -> None:
    view = env.open(
        "SRM-30ml", 79900, reason_category="not_as_described", desired_resolution="refund"
    )
    case_id = view["case_id"]
    env.confirm(case_id)
    env.relay.drain()
    refund = env.db.scalars(select(Refund).where(Refund.case_id == case_id)).one()
    r = signed_post(
        env.client,
        "/api/v1/webhooks/payment",
        {"event_id": "pay-1", "gateway_ref": refund.gateway_ref, "status": "failed"},
    )
    assert r.status_code == 200 and r.json() == {"result": "applied"}
    env.db.expire_all()
    assert env.db.get(Refund, refund.id).status == "failed"  # type: ignore[union-attr]
    env.relay.drain()
    assert "refund_failed" in sent_templates(env, case_id, audience="staff")


# --- Timers and SLAs ---


def test_customer_reminder_then_inactivity_close(env: Env) -> None:
    view = env.open("KUR-M", 129900)  # no reason given and no LLM: the agent must ask
    case_id = view["case_id"]
    assert view["waiting_for"] == "customer_message"
    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    now = datetime.now(UTC)
    timers.tick(sessions, env.runner.timeout, now + timedelta(hours=25))
    env.relay.drain()
    assert "reminder" in sent_templates(env, case_id)
    assert env.case(case_id).status == "waiting"
    timers.tick(sessions, env.runner.timeout, now + timedelta(days=7, minutes=1))
    case = env.case(case_id)
    assert case.status == "closed"
    open_timers = env.db.scalars(
        select(SlaTimer).where(
            SlaTimer.case_id == case_id, SlaTimer.fired_at.is_(None), SlaTimer.resolved_at.is_(None)
        )
    ).all()
    assert open_timers == []  # closing settles every clock


def test_sla_breach_raises_priority_and_alerts_staff(env: Env) -> None:
    view = env.open(
        "PHN-blue", 4299900, reason_category="defective", desired_resolution="replacement"
    )
    case_id = view["case_id"]
    # High value + evidence required: the case waits; replying met the response SLAs.
    env.db.expire_all()
    first = env.db.scalars(
        select(SlaTimer).where(SlaTimer.case_id == case_id, SlaTimer.kind == "sla:first_response")
    ).one()
    assert first.resolved_at is not None
    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    # Close the customer-timeout path by answering, so only the SLA clocks remain due.
    timers.resolve(env.db, env.case(case_id).id, datetime.now(UTC), prefix="customer_")
    env.db.commit()
    timers.tick(sessions, env.runner.timeout, datetime.now(UTC) + timedelta(days=8))
    case = env.case(case_id)
    assert case.priority == 1
    breaches = env.db.scalars(
        select(AuditEvent).where(AuditEvent.case_id == case_id, AuditEvent.action == "sla.breached")
    ).all()
    assert [b.payload["sla"] for b in breaches] == ["sla:resolution"]
    env.relay.drain()
    assert "sla_breached" in sent_templates(env, case_id, audience="staff")
