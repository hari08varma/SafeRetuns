"""Customer case API end to end: graph + LLM (fake) + responder + verifier + storage."""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.adapters.mock import MockCarrierAdapter, MockInventoryAdapter
from returns_agent.agent.cases import CaseService
from returns_agent.api.app import create_app
from returns_agent.api.deps import Adapters
from returns_agent.audit import log as audit
from returns_agent.config import config_dir
from returns_agent.db.models import AuditEvent, Customer, Message, Order, OrderItem, ReturnCase
from returns_agent.db.session import get_engine
from returns_agent.graph.nodes import build_handlers
from returns_agent.graph.registry import GraphRegistry
from returns_agent.graph.runner import CaseRunner, make_feasibility
from returns_agent.llm.fake import FakeProvider
from returns_agent.security.pii import decrypt
from returns_agent.security.tokens import Principal, Role, issue_token
from tests.conftest import needs_db

pytestmark = [needs_db]

EXTRACTION = json.dumps(
    {
        "reason_category": "size_fit",
        "desired_resolution": "refund",
        "exchange_variant": None,
        "language": "en",
        "sentiment": "calm",
        "wants_human": False,
        "legal_threat": False,
    }
)


@pytest.fixture
def saver() -> Iterator[PostgresSaver]:
    with PostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as s:
        s.setup()
        yield s


def build_client(saver: PostgresSaver, adapters: Adapters, llm: FakeProvider | None) -> TestClient:
    registry = GraphRegistry(
        config_dir() / "graphs",
        "returns-v1",
        partial(build_handlers, llm=llm),
        make_feasibility(MockInventoryAdapter({"KUR-L": 3}), MockCarrierAdapter()),
        saver,
    )
    service = CaseService(CaseRunner(registry, get_engine()), llm)
    return TestClient(create_app(adapters, cases=service))


@pytest.fixture
def fresh_order(seeded_db: Session) -> tuple[Customer, Order, OrderItem]:
    customer = seeded_db.scalars(select(Customer)).first()
    assert customer is not None
    now = datetime.now(UTC)
    order = Order(
        external_id="ORD-T1",
        customer_id=customer.id,
        placed_at=now - timedelta(days=8),
        delivered_at=now - timedelta(days=5),
        status="delivered",
        payment_method="upi",
        coupon_minor=0,
        total_minor=129900,
    )
    order.items = [
        OrderItem(
            external_id="ORD-T1-1",
            sku="KUR-M",
            qty=1,
            unit_price_minor=129900,
            discount_alloc_minor=0,
            final_sale=False,
        )
    ]
    seeded_db.add(order)
    seeded_db.commit()
    return customer, order, order.items[0]


def headers(customer: Customer) -> dict[str, str]:
    token = issue_token(Principal(str(customer.id), Role.CUSTOMER), "access")
    return {"Authorization": f"Bearer {token}"}


def test_open_case_with_llm_then_confirm(
    saver: PostgresSaver,
    adapters: Adapters,
    seeded_db: Session,
    fresh_order: tuple[Customer, Order, OrderItem],
) -> None:
    customer, _, _ = fresh_order
    phone, name = decrypt(customer.phone_enc), decrypt(customer.name_enc)
    fake = FakeProvider(
        [
            EXTRACTION,
            EXTRACTION,
            EXTRACTION,  # understand x3
            '{"message": "You can choose an exchange, store credit or a refund of ₹1,299.00."}',
            '{"violations": []}',  # verifier
            '{"message": "Done. Your pickup is booked."}',
            '{"violations": []}',
        ]
    )
    client = build_client(saver, adapters, fake)
    body = {
        "order_id": "ORD-T1",
        "item_id": "ORD-T1-1",
        "message": f"Hi, I'm {name} ({phone}). The kurta is too tight, refund please.",
    }
    r = client.post("/api/v1/cases", json=body, headers=headers(customer))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["waiting_for"] == "customer_confirm" and view["refund_total_minor"] == 129900
    assert view["options"] == ["exchange", "store_credit", "refund"]
    assert "₹1,299.00" in view["reply"]

    r = client.post(
        f"/api/v1/cases/{view['case_id']}/confirm", json={"accept": True}, headers=headers(customer)
    )
    assert r.status_code == 200 and r.json()["current_node"] == "SCHEDULE_PICKUP"
    assert r.json()["waiting_for"] == "action_result"  # the relay books the pickup

    sent = json.dumps([c.messages for c in fake.calls], ensure_ascii=False)
    assert phone not in sent and name not in sent  # PII never reached the model

    history = client.get(
        f"/api/v1/cases/{view['case_id']}/messages", headers=headers(customer)
    ).json()
    assert [m["role"] for m in history] == ["customer", "agent", "agent"]
    seeded_db.expire_all()
    redacted = seeded_db.scalars(select(Message.redacted_content)).all()
    assert all(phone not in (t or "") for t in redacted)

    case_id = view["case_id"]
    events = seeded_db.scalars(select(AuditEvent).where(AuditEvent.chain_key == case_id)).all()
    understand_node = next(e for e in events if e.payload.get("node") == "UNDERSTAND_REQUEST")
    assert understand_node.payload["llm"]["prompt_refs"] == ["system@1", "understand_request@1"]
    sent_events = [e for e in events if e.action == "message.sent"]
    assert sent_events and sent_events[0].payload["used_template"] is False
    case = seeded_db.get(ReturnCase, sent_events[0].case_id)
    assert case is not None and audit.verify(seeded_db, case.id).ok


def test_without_llm_uses_templates_and_clarifies(
    saver: PostgresSaver, adapters: Adapters, fresh_order: tuple[Customer, Order, OrderItem]
) -> None:
    customer, _, _ = fresh_order
    client = build_client(saver, adapters, None)
    body = {"order_id": "ORD-T1", "item_id": "ORD-T1-1", "message": "I want to return this"}
    view = client.post("/api/v1/cases", json=body, headers=headers(customer)).json()
    assert view["waiting_for"] == "customer_message"
    assert view["reply"] == "Could you tell me why you'd like to return the item?"
    # Confirming while the case waits for a message is rejected, not applied.
    r = client.post(
        f"/api/v1/cases/{view['case_id']}/confirm", json={"accept": True}, headers=headers(customer)
    )
    assert r.status_code == 409


def test_other_customers_cannot_see_or_touch_a_case(
    saver: PostgresSaver,
    adapters: Adapters,
    seeded_db: Session,
    fresh_order: tuple[Customer, Order, OrderItem],
) -> None:
    customer, _, _ = fresh_order
    other = seeded_db.scalars(select(Customer).where(Customer.id != customer.id)).first()
    assert other is not None
    client = build_client(saver, adapters, None)
    body = {"order_id": "ORD-T1", "item_id": "ORD-T1-1", "message": "return"}
    assert client.post("/api/v1/cases", json=body, headers=headers(other)).status_code == 404
    case_id = client.post("/api/v1/cases", json=body, headers=headers(customer)).json()["case_id"]
    assert (
        client.get(f"/api/v1/cases/{case_id}/messages", headers=headers(other)).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/cases/{case_id}/messages", json={"text": "x"}, headers=headers(other)
        ).status_code
        == 404
    )


def test_legal_threat_escalates(
    saver: PostgresSaver, adapters: Adapters, fresh_order: tuple[Customer, Order, OrderItem]
) -> None:
    customer, _, _ = fresh_order
    threat = EXTRACTION.replace('"legal_threat": false', '"legal_threat": true')
    fake = FakeProvider(
        [threat] * 3 + ['{"message": "A specialist will review this."}', '{"violations": []}']
    )
    client = build_client(saver, adapters, fake)
    body = {
        "order_id": "ORD-T1",
        "item_id": "ORD-T1-1",
        "message": "Refund now or I'm going to consumer court",
    }
    view = client.post("/api/v1/cases", json=body, headers=headers(customer)).json()
    assert view["status"] == "escalated" and view["current_node"] == "ESCALATE"
