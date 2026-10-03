"""Phase 9: human in the loop — queues, handoff, approvals with authority limits and a
single-use token, two-person rule, escalation resolution, goodwill and denial reviews."""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.adapters.bundle import Adapters
from returns_agent.adapters.mock import MockPaymentAdapter
from returns_agent.agent.cases import CaseService
from returns_agent.api.app import create_app
from returns_agent.config import config_dir
from returns_agent.db.models import (
    Approval,
    AuditEvent,
    Customer,
    Goodwill,
    Message,
    Notification,
    Order,
    OrderItem,
    Outbox,
    QueueItem,
    Refund,
    ReturnCase,
    Shipment,
    StaffUser,
)
from returns_agent.db.session import get_engine
from returns_agent.evals.harness import mock_adapters
from returns_agent.evidence.store import LocalEvidenceStore
from returns_agent.execution.relay import Relay
from returns_agent.execution.webhooks import CarrierWebhook, handle_carrier
from returns_agent.graph.nodes import build_handlers
from returns_agent.graph.registry import GraphRegistry
from returns_agent.graph.runner import CaseRunner, make_feasibility
from returns_agent.hitl import approvals, queues
from returns_agent.security.pii import decrypt
from returns_agent.security.tokens import Principal, Role, hash_password, issue_token
from tests.conftest import needs_db

pytestmark = [needs_db]
_ids = count(1)


@dataclass
class World:
    client: TestClient
    runner: CaseRunner
    relay: Relay
    adapters: Adapters
    db: Session
    customer: Customer

    def staff(self, role: Role, limit: int | None = None) -> StaffUser:
        n = next(_ids)
        user = StaffUser(
            email=f"{role.value}-{n}-{uuid.uuid4().hex[:6]}@saferetuns.dev",
            password_hash=hash_password("not-used-in-tests"),
            role=role.value,
            authority_limit_minor=limit
            if limit is not None
            else {"agent": 200_000}.get(role.value, 2_500_000),
        )
        self.db.add(user)
        self.db.commit()
        return user

    def open(self, sku: str, price: int, reason: str, desired: str, **kw: Any) -> str:
        ref = f"ORD-H{next(_ids):04d}"
        now = datetime.now(UTC)
        order = Order(
            external_id=ref,
            customer_id=self.customer.id,
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
                final_sale=kw.pop("final_sale", False),
            )
        ]
        self.db.add(order)
        self.db.commit()
        body = {
            "order_id": ref,
            "item_id": f"{ref}-1",
            "message": "please help",
            "reason_category": reason,
            "desired_resolution": desired,
        }
        r = self.client.post("/api/v1/cases", json=body, headers=self.as_customer())
        assert r.status_code == 201, r.text
        return str(r.json()["case_id"])

    def as_customer(self) -> dict[str, str]:
        return auth(Principal(str(self.customer.id), Role.CUSTOMER))

    def decide(self, case_id: str, staff: StaffUser, **body: Any) -> Any:
        return self.client.post(
            f"/api/v1/console/cases/{case_id}/approval",
            json={"decision": "approve", "reason_code": "policy_compliant", **body},
            headers=as_staff(staff),
        )

    def case(self, case_id: str) -> ReturnCase:
        self.db.expire_all()
        case = self.db.get(ReturnCase, uuid.UUID(case_id))
        assert case is not None
        return case

    def approval(self, case_id: str) -> Approval:
        self.db.expire_all()
        return self.db.scalars(select(Approval).where(Approval.case_id == uuid.UUID(case_id))).one()

    def carrier(self, case_id: str, event: str) -> None:
        sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
        with sessions() as s:
            awb = s.scalars(
                select(Shipment.awb).where(Shipment.case_id == uuid.UUID(case_id))
            ).one()
            hook = CarrierWebhook.model_validate(
                {"event_id": uuid.uuid4().hex, "awb": awb, "event": event}
            )
            handle_carrier(s, self.runner, hook)


def auth(principal: Principal) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(principal, 'access')}"}


def as_staff(staff: StaffUser) -> dict[str, str]:
    return auth(Principal(str(staff.id), Role(staff.role)))


@pytest.fixture
def world(seeded_db: Session, tmp_path: Path) -> Iterator[World]:
    adapters = mock_adapters()
    adapters.inventory.stock = {"KUR-L": 5, "PHN-blue": 5}  # type: ignore[attr-defined]
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
        service = CaseService(runner, None, LocalEvidenceStore(tmp_path))
        customer = seeded_db.scalars(select(Customer)).first()
        assert customer is not None
        yield World(
            TestClient(create_app(adapters, cases=service)),
            runner,
            Relay(get_engine(), runner, adapters),
            adapters,
            seeded_db,
            customer,
        )


def smartwatch_case(w: World) -> str:
    """₹5,999 refund: above the ₹5,000 automation limit, so it needs one approver."""
    case_id = w.open("SWT-black", 599900, "not_as_described", "refund")
    assert w.case(case_id).current_node == "HUMAN_APPROVAL"
    return case_id


# --- Queue + approval end to end ----------------------------------------------------------------


def test_approval_queue_then_approved_refund_consumes_the_token(world: World) -> None:
    w = world
    case_id = smartwatch_case(w)
    item = w.db.scalars(select(QueueItem).where(QueueItem.case_id == uuid.UUID(case_id))).one()
    assert (item.queue, item.status) == ("approval", "open")
    assert abs(item.due_at - item.created_at - queues.SLA["approval"]) < timedelta(minutes=1)
    approval = w.approval(case_id)
    assert (approval.amount_minor, approval.required_approvals) == (599900, 1)

    approver = w.staff(Role.APPROVER)
    assert w.decide(case_id, approver, reason_code="because").status_code == 422
    r = w.decide(case_id, approver, note="photos match")
    assert r.status_code == 200, r.text
    assert r.json()["approval"] == "approved"
    assert r.json()["case"]["waiting_for"] == "customer_confirm"
    assert r.json()["case"]["options"] == ["refund"]  # only what was approved
    approval = w.approval(case_id)
    assert approval.token_hash and approval.consumed_at is None and approval.signoffs[0]["note"]

    confirm = w.client.post(
        f"/api/v1/cases/{case_id}/confirm", json={"accept": True}, headers=w.as_customer()
    )
    assert confirm.status_code == 200, confirm.text
    w.relay.drain()
    w.carrier(case_id, "received")
    qc = w.client.post(
        f"/api/v1/console/cases/{case_id}/qc",
        json={"passed": True},
        headers=as_staff(w.staff(Role.QC_OPERATOR)),
    )
    assert qc.status_code == 200, qc.text
    w.relay.drain()
    assert w.case(case_id).status == "closed"
    assert w.approval(case_id).consumed_at is not None
    refund = w.db.scalars(select(Refund).where(Refund.case_id == uuid.UUID(case_id))).one()
    assert refund.amount_minor == 599900
    signoff = w.db.scalars(
        select(AuditEvent).where(
            AuditEvent.case_id == uuid.UUID(case_id), AuditEvent.action == "approval.signoff"
        )
    ).one()
    assert signoff.actor_type == "staff" and signoff.actor_id == str(approver.id)
    assert signoff.payload["role"] == "approver"
    assert signoff.payload["reason_code"] == "policy_compliant"


def test_execution_refuses_a_forged_token(world: World) -> None:
    """AC: no refund without a valid token when the route is approval."""
    w = world
    case_id = w.open("SWT-black", 599900, "not_as_described", "refund")
    w.decide(case_id, w.staff(Role.APPROVER))
    w.client.post(
        f"/api/v1/cases/{case_id}/confirm", json={"accept": True}, headers=w.as_customer()
    )
    w.relay.drain()
    w.carrier(case_id, "received")
    w.client.post(
        f"/api/v1/console/cases/{case_id}/qc",
        json={"passed": True},
        headers=as_staff(w.staff(Role.QC_OPERATOR)),
    )
    w.db.expire_all()
    row = w.db.scalars(
        select(Outbox).where(Outbox.case_id == uuid.UUID(case_id), Outbox.action == "refund")
    ).one()
    approval_id = w.approval(case_id).id
    row.payload = {**row.payload, "approval_token": f"{approval_id}.{'0' * 64}"}
    w.db.commit()
    w.relay.drain()
    w.db.expire_all()
    assert w.db.scalars(select(Refund).where(Refund.case_id == uuid.UUID(case_id))).first() is None
    assert w.db.get(Outbox, row.id).status == "failed"  # type: ignore[union-attr]
    assert w.db.get(Outbox, row.id).attempts == 5  # type: ignore[union-attr]  # refused, never retried
    assert w.case(case_id).status == "escalated"
    assert w.approval(case_id).consumed_at is None


def test_tokens_are_single_use_and_bound_to_action_and_amount(world: World) -> None:
    w = world
    case_id = smartwatch_case(w)
    w.decide(case_id, w.staff(Role.APPROVER))
    token = w.runner.facts(uuid.UUID(case_id))["approval"]["token"]
    now = datetime.now(UTC)
    cid = uuid.UUID(case_id)
    with pytest.raises(approvals.TokenInvalid, match="covers refund"):
        approvals.consume(w.db, cid, token, "create_exchange", 0, now)
    with pytest.raises(approvals.TokenInvalid, match="larger"):
        approvals.consume(w.db, cid, token, "refund", 599901, now)
    with pytest.raises(approvals.TokenInvalid):
        approvals.consume(w.db, uuid.uuid4(), token, "refund", 599900, now)  # other case
    with pytest.raises(approvals.TokenInvalid):
        approvals.consume(w.db, cid, None, "refund", 599900, now)
    approvals.consume(w.db, cid, token, "refund", 599900, now)
    with pytest.raises(approvals.TokenInvalid, match="already used"):
        approvals.consume(w.db, cid, token, "refund", 599900, now)
    w.db.rollback()


def test_authority_limit_and_role(world: World) -> None:
    w = world
    case_id = smartwatch_case(w)
    low = w.staff(Role.APPROVER, limit=500_000)  # ₹5,000 < ₹5,999
    assert w.decide(case_id, low).status_code == 403
    agent = w.staff(Role.AGENT)
    assert w.decide(case_id, agent).status_code == 403  # agents cannot approve at all
    assert w.approval(case_id).status == "pending"


def test_modify_and_reject(world: World) -> None:
    w = world
    case_id = smartwatch_case(w)
    approver = w.staff(Role.APPROVER)
    bad = w.decide(
        case_id, approver, decision="modify", reason_code="better_resolution", option="x"
    )
    assert bad.status_code == 422
    r = w.decide(
        case_id, approver, decision="modify", reason_code="better_resolution", option="store_credit"
    )
    assert r.status_code == 200, r.text
    assert r.json()["case"]["options"] == ["store_credit"]
    assert w.approval(case_id).requested_action == "store_credit"

    other = smartwatch_case(w)
    rejected = w.decide(other, approver, decision="reject", reason_code="insufficient_evidence")
    assert rejected.status_code == 200 and rejected.json()["case"]["status"] == "closed"
    assert (
        w.db.scalars(select(QueueItem).where(QueueItem.case_id == uuid.UUID(other))).one().outcome
        == "rejected"
    )
    assert w.decide(other, approver).status_code == 409  # nothing left to decide


def test_two_person_rule_above_the_limit(world: World) -> None:
    w = world
    case_id = w.open("PHN-blue", 4299900, "not_as_described", "refund")
    approval = w.approval(case_id)
    assert (approval.amount_minor, approval.required_approvals) == (4299900, 2)
    first, second = w.staff(Role.APPROVER), w.staff(Role.ADMIN)
    r = w.decide(case_id, first)
    assert r.json()["approval"] == "partial" and "case" not in r.json()
    assert w.case(case_id).current_node == "HUMAN_APPROVAL"  # nothing moves on one signature
    assert w.decide(case_id, first).status_code == 409  # same person twice
    disagree = w.decide(
        case_id, second, decision="modify", reason_code="better_resolution", option="store_credit"
    )
    assert disagree.status_code == 409
    done = w.decide(case_id, second, reason_code="evidence_sufficient")
    assert done.json()["approval"] == "approved"
    assert done.json()["case"]["waiting_for"] == "customer_confirm"
    assert [s["role"] for s in w.approval(case_id).signoffs] == ["approver", "admin"]


# --- Escalations, fraud review, neutral messaging ----------------------------------------------


def serial_returner(w: World) -> str:
    now = datetime.now(UTC)
    for _ in range(6):
        ref = f"ORD-SR{next(_ids):04d}"
        order = Order(
            external_id=ref,
            customer_id=w.customer.id,
            placed_at=now - timedelta(days=30),
            delivered_at=now - timedelta(days=28),
            status="delivered",
            payment_method="upi",
            coupon_minor=0,
            total_minor=59900,
        )
        w.db.add(order)
        w.db.flush()
        w.db.add(
            ReturnCase(
                customer_id=w.customer.id,
                order_id=order.id,
                graph_version="returns-v1",
                status="closed",
            )
        )
    w.db.commit()
    return w.open("KUR-M", 129900, "size_fit", "refund")


def test_fraud_review_queue_neutral_message_and_resolution(world: World) -> None:
    w = world
    case_id = serial_returner(w)
    assert w.case(case_id).status == "escalated"
    item = w.db.scalars(select(QueueItem).where(QueueItem.case_id == uuid.UUID(case_id))).one()
    assert item.queue == "fraud_review"
    last = w.db.scalars(
        select(Message)
        .where(Message.case_id == uuid.UUID(case_id))
        .order_by(Message.created_at.desc())
    ).first()
    assert last is not None and "specialist" in last.content
    assert not any(
        word in last.content.lower() for word in ("fraud", "suspic", "risk", "dishonest")
    )

    agent = w.staff(Role.AGENT)
    bad = w.client.post(
        f"/api/v1/console/cases/{case_id}/resolve",
        json={"outcome": "rejected", "reason_code": "nope"},
        headers=as_staff(agent),
    )
    assert bad.status_code == 422
    r = w.client.post(
        f"/api/v1/console/cases/{case_id}/resolve",
        json={"outcome": "rejected", "reason_code": "fraud_confirmed", "note": "same photos"},
        headers=as_staff(agent),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "closed"
    w.db.expire_all()
    assert w.db.get(Customer, w.customer.id).risk_profile["confirmed_fraud"] is True  # type: ignore[union-attr]
    assert w.db.get(QueueItem, item.id).status == "done"  # type: ignore[union-attr]
    resolved = w.db.scalars(
        select(AuditEvent).where(
            AuditEvent.case_id == uuid.UUID(case_id), AuditEvent.action == "case.resolved"
        )
    ).one()
    assert (
        resolved.payload["reason_code"] == "fraud_confirmed" and resolved.payload["role"] == "agent"
    )


# --- Queues: listing, claiming, SLA breach ----------------------------------------------------


def test_queue_listing_claiming_and_sla_breach(world: World) -> None:
    w = world
    low = smartwatch_case(w)
    high = w.open("PHN-blue", 4299900, "not_as_described", "refund")
    approver, other = w.staff(Role.APPROVER), w.staff(Role.APPROVER)
    listed = w.client.get("/api/v1/console/queues/approval", headers=as_staff(approver)).json()
    assert [i["case_id"] for i in listed if i["case_id"] in (low, high)] == [high, low]  # priority
    assert (
        w.client.get("/api/v1/console/queues/nope", headers=as_staff(approver)).status_code == 404
    )
    item_id = next(i["id"] for i in listed if i["case_id"] == low)
    claimed = w.client.post(
        f"/api/v1/console/queue-items/{item_id}/claim", headers=as_staff(approver)
    )
    assert claimed.json()["status"] == "assigned" and claimed.json()["assignee_id"] == str(
        approver.id
    )
    taken = w.client.post(f"/api/v1/console/queue-items/{item_id}/claim", headers=as_staff(other))
    assert taken.status_code == 409

    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    assert queues.tick(sessions, datetime.now(UTC) + timedelta(hours=1)) == 0
    breached = queues.tick(sessions, datetime.now(UTC) + timedelta(hours=5))
    assert breached >= 2
    assert queues.tick(sessions, datetime.now(UTC) + timedelta(hours=6)) == 0  # once per item
    w.db.expire_all()
    item = w.db.get(QueueItem, uuid.UUID(item_id))
    assert item is not None and item.escalated_at is not None and item.priority >= 100
    alert = w.db.scalars(
        select(Notification).where(
            Notification.case_id == uuid.UUID(low), Notification.audience == "supervisor"
        )
    ).one()
    assert alert.template == "queue_sla_breached"
    w.relay.drain()
    w.db.expire_all()
    sent = w.adapters.notification.sent  # type: ignore[attr-defined]
    assert any(m["to"] == "supervisor-queue" for m in sent)
    assert decrypt(w.customer.phone_enc) not in {
        m["to"] for m in sent if m["template"] == "queue_sla_breached"
    }


# --- Handoff packet --------------------------------------------------------------------------


def test_handoff_packet(world: World) -> None:
    w = world
    case_id = smartwatch_case(w)
    r = w.client.get(
        f"/api/v1/console/cases/{case_id}/handoff", headers=as_staff(w.staff(Role.AGENT))
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert "not as described" in p["summary"] and "Route approval" in p["summary"]
    assert p["suggested"]["option"] == "refund" and p["suggested"]["alternatives"]
    assert p["decision"]["route"] == "approval" and p["approval"]["status"] == "pending"
    assert any(c["text"] for c in p["policy"]["clauses"])
    assert p["queue"][0]["queue"] == "approval" and p["timeline"]
    phone = decrypt(w.customer.phone_enc)
    assert phone not in r.text and p["customer"]["phone"].endswith(phone[-4:])


# --- Goodwill ------------------------------------------------------------------------------------


def test_goodwill_within_authority_and_case_cap(world: World) -> None:
    w = world
    case_id = smartwatch_case(w)
    agent, approver = w.staff(Role.AGENT), w.staff(Role.APPROVER)
    url = f"/api/v1/console/cases/{case_id}/goodwill"
    over = w.client.post(
        url, json={"amount_minor": 250_000, "reason_code": "delay_apology"}, headers=as_staff(agent)
    )
    assert over.status_code == 403  # agent limit ₹2,000
    assert (
        w.client.post(
            url, json={"amount_minor": 1000, "reason_code": "x"}, headers=as_staff(agent)
        ).status_code
        == 422
    )
    ok = w.client.post(
        url, json={"amount_minor": 150_000, "reason_code": "courier_issue"}, headers=as_staff(agent)
    )
    assert ok.status_code == 201, ok.text
    capped = w.client.post(
        url,
        json={"amount_minor": 400_000, "reason_code": "service_failure"},
        headers=as_staff(approver),
    )
    assert capped.status_code == 403 and "capped" in capped.text  # ₹5,000 per case
    w.relay.drain()
    w.db.expire_all()
    grant = w.db.scalars(select(Goodwill).where(Goodwill.case_id == uuid.UUID(case_id))).one()
    assert grant.status == "succeeded" and grant.gateway_ref
    payment = w.adapters.payment
    assert isinstance(payment, MockPaymentAdapter)
    assert any(m == "store_credit" and a == 150_000 for _, a, m in payment.refunds.values())
    assert w.db.scalars(select(Refund).where(Refund.case_id == uuid.UUID(case_id))).first() is None
    assert w.case(case_id).current_node == "HUMAN_APPROVAL"  # goodwill never moves the case


# --- Customer review of an automated denial ----------------------------------------------------


def test_customer_can_request_review_of_an_automated_denial(world: World) -> None:
    w = world
    case_id = w.open("KUR-L", 129900, "size_fit", "refund", final_sale=True)
    assert w.case(case_id).status == "closed"
    url = f"/api/v1/cases/{case_id}/review"
    r = w.client.post(url, json={"reason": "It was not marked final sale"}, headers=w.as_customer())
    assert r.status_code == 202, r.text
    assert w.client.post(url, json={"reason": "again"}, headers=w.as_customer()).status_code == 409
    item = w.db.scalars(
        select(QueueItem).where(
            QueueItem.case_id == uuid.UUID(case_id), QueueItem.queue == "review"
        )
    ).one()
    history = w.client.get(f"/api/v1/cases/{case_id}/messages", headers=w.as_customer()).json()
    assert history[-1]["text"].startswith("Thanks — a member of our team will review")

    approved = smartwatch_case(w)  # not a denial
    assert (
        w.client.post(
            f"/api/v1/cases/{approved}/review", json={"reason": "x"}, headers=w.as_customer()
        ).status_code
        == 409
    )

    staff = w.staff(Role.AGENT)
    close = f"/api/v1/console/queue-items/{item.id}/close"
    assert (
        w.client.post(
            close, json={"outcome": "upheld", "reason_code": "x"}, headers=as_staff(staff)
        ).status_code
        == 422
    )
    done = w.client.post(
        close,
        json={"outcome": "overturned", "reason_code": "policy_misapplied"},
        headers=as_staff(staff),
    )
    assert done.status_code == 200 and done.json()["outcome"] == "overturned"
