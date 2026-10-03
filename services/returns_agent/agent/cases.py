"""Customer case flow: open a case, take messages and confirmations, reply with a verified
message. Every customer and agent message is stored (with a redacted copy) and audited."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from returns_agent.agent.context import redactor_for
from returns_agent.agent.respond import reply
from returns_agent.audit import log as audit
from returns_agent.config import get_settings
from returns_agent.db.models import (
    Address,
    Customer,
    Exchange,
    Message,
    Order,
    OrderItem,
    Product,
    QueueItem,
    Refund,
    ReplacementOrder,
    ReturnCase,
    ReturnItem,
)
from returns_agent.evidence.service import Upload, ingest, vision_assessment
from returns_agent.evidence.store import EvidenceStore, LocalEvidenceStore
from returns_agent.graph.runner import CaseRunner, Event, RunResult, StaleEvent
from returns_agent.hitl import queues
from returns_agent.lifecycle import timers
from returns_agent.lifecycle.notify import notify
from returns_agent.llm.client import LLMClient
from returns_agent.security.pii import decrypt

DEAD = ("failed", "cancelled")  # execution rows that did not result in a return


class CaseNotFound(Exception):
    pass


class ReviewNotAvailable(Exception):
    pass


@dataclass(frozen=True)
class CaseView:
    case_id: str
    status: str
    current_node: str
    waiting_for: str | None
    reply: str
    options: list[str]
    refund_total_minor: int | None


def _pii(session: Session, customer: Customer) -> dict[str, str]:
    address = session.scalars(select(Address).where(Address.customer_id == customer.id)).first()
    return {
        "name": decrypt(customer.name_enc),
        "phone": decrypt(customer.phone_enc),
        "email": decrypt(customer.email_enc),
        "address": decrypt(address.address_enc) if address else "",
    }


def customer_stats(
    session: Session, customer: Customer, exclude_case: uuid.UUID | None = None
) -> dict[str, int]:
    since = datetime.now(UTC) - timedelta(days=90)
    returns = (
        select(func.count())
        .select_from(ReturnCase)
        .where(ReturnCase.customer_id == customer.id, ReturnCase.created_at >= since)
    )
    if exclude_case is not None:
        returns = returns.where(ReturnCase.id != exclude_case)
    orders = (
        select(func.count())
        .select_from(Order)
        .where(Order.customer_id == customer.id, Order.placed_at >= since)
    )
    damage = (
        select(func.count())
        .select_from(ReturnItem)
        .join(ReturnCase, ReturnCase.id == ReturnItem.case_id)
        .where(
            ReturnCase.customer_id == customer.id,
            ReturnCase.created_at >= since,
            ReturnItem.reason_category.in_(("damaged", "defective")),
        )
    )
    if exclude_case is not None:
        damage = damage.where(ReturnCase.id != exclude_case)
    return {
        "returns_90d": session.scalar(returns) or 0,
        "orders_90d": session.scalar(orders) or 0,
        "account_age_days": customer.account_age_days,
        "damage_claims_90d": session.scalar(damage) or 0,
        "linked_risky_accounts": linked_risky_accounts(session, customer, since),
        "confirmed_fraud": bool((customer.risk_profile or {}).get("confirmed_fraud")),
    }


LINKED_RETURNS = 2  # a linked account counts as risky from this many returns in 90 days


def linked_risky_accounts(session: Session, customer: Customer, since: datetime) -> int:
    """Other accounts sharing this customer's email or a delivery address (blind indexes)
    that return a lot or had fraud confirmed. Matching never decrypts anything."""
    addresses = select(Address.address_index).where(
        Address.customer_id == customer.id, Address.address_index.is_not(None)
    )
    linked_ids = set(
        session.scalars(
            select(Customer.id).where(
                Customer.id != customer.id, Customer.email_index == customer.email_index
            )
        )
    ) | set(
        session.scalars(
            select(Address.customer_id).where(
                Address.customer_id != customer.id, Address.address_index.in_(addresses)
            )
        )
    )
    risky = 0
    for linked in linked_ids:
        other = session.get(Customer, linked)
        recent = session.scalar(
            select(func.count())
            .select_from(ReturnCase)
            .where(ReturnCase.customer_id == linked, ReturnCase.created_at >= since)
        )
        if (recent or 0) >= LINKED_RETURNS or (other and other.risk_profile.get("confirmed_fraud")):
            risky += 1
    return risky


def returned_qty(
    session: Session, order_item_id: uuid.UUID, exclude_case: uuid.UUID | None = None
) -> int:
    """Units of this order line already returned, or in a return that is still open."""
    executed = or_(
        *(
            exists(select(t.id).where(t.case_id == ReturnCase.id, t.status.not_in(DEAD)))
            for t in (Refund, Exchange, ReplacementOrder)
        )
    )
    query = (
        select(func.coalesce(func.sum(ReturnItem.qty), 0))
        .join(ReturnCase, ReturnCase.id == ReturnItem.case_id)
        .where(ReturnItem.order_item_id == order_item_id)
        .where(or_(ReturnCase.status != "closed", executed))
    )
    if exclude_case is not None:
        query = query.where(ReturnCase.id != exclude_case)
    return int(session.scalar(query) or 0)


def build_case_facts(
    session: Session,
    customer: Customer,
    order: Order,
    item: OrderItem,
    qty: int,
    message: str,
    case_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    product = session.scalars(select(Product).where(Product.sku == item.sku)).one()
    family = item.sku.rsplit("-", 1)[0]
    variants = session.scalars(
        select(Product.sku).where(Product.sku.startswith(f"{family}-")).order_by(Product.sku)
    ).all()
    address = session.scalars(select(Address).where(Address.customer_id == customer.id)).first()
    already = returned_qty(session, item.id, exclude_case=case_id)
    return {
        "customer_id": str(customer.id),
        "pincode": address.pincode if address else None,
        "pii": _pii(session, customer),
        "conversation": [{"role": "customer", "text": message}],
        "order": {
            "order_id": order.external_id,
            "status": order.status,
            "payment_method": order.payment_method,
            "placed_at": order.placed_at.isoformat(),
            "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        },
        "item": {
            "item_id": item.external_id,
            "sku": item.sku,
            "category": product.category,
            "final_sale": item.final_sale,
            "variants": list(variants),  # SKUs this item can be exchanged for
            "qty_ordered": item.qty,
            "qty_returning": qty,
            "qty_already_returned": already,
        },
        "request": {},
        "customer_stats": customer_stats(session, customer, exclude_case=case_id),
        "pricing": {
            "unit_price_minor": item.unit_price_minor,
            "line_discount_minor": item.discount_alloc_minor,
            "payments": [{"method": order.payment_method, "amount_minor": order.total_minor}],
        },
    }


class CaseService:
    def __init__(
        self, runner: CaseRunner, llm: LLMClient | None, store: EvidenceStore | None = None
    ) -> None:
        self.runner = runner
        self.llm = llm
        self.store = store or LocalEvidenceStore(get_settings().evidence_dir)

    def open(
        self,
        session: Session,
        customer_id: uuid.UUID,
        order_ref: str,
        item_ref: str,
        qty: int,
        message: str,
        request: dict[str, Any] | None = None,
    ) -> CaseView:
        customer = session.get(Customer, customer_id)
        order = session.scalars(
            select(Order).where(Order.external_id == order_ref, Order.customer_id == customer_id)
        ).first()
        item = session.scalars(select(OrderItem).where(OrderItem.external_id == item_ref)).first()
        if customer is None or order is None or item is None or item.order_id != order.id:
            raise CaseNotFound("order or item not found for this customer")
        case = ReturnCase(
            customer_id=customer.id,
            order_id=order.id,
            graph_version=self.runner.registry.active_version,
        )
        session.add(case)
        session.flush()
        facts = build_case_facts(session, customer, order, item, qty, message, case.id)
        facts["principal_customer_id"] = str(customer_id)
        facts["request"] = {k: v for k, v in (request or {}).items() if v}  # UI selections
        session.add(
            ReturnItem(
                case_id=case.id,
                order_item_id=item.id,
                qty=qty,
                reason_category=facts["request"].get("reason_category"),
            )
        )
        self._store(session, case.id, "customer", message, facts)
        session.commit()
        result = self.runner.start(case.id, facts)
        return self._respond(session, result)

    def message(
        self, session: Session, customer_id: uuid.UUID, case_id: uuid.UUID, text: str
    ) -> CaseView:
        self._owned(session, customer_id, case_id)
        result = self.runner.dispatch(
            case_id, Event("customer_message", {"text": text}, "customer", str(customer_id))
        )
        self._store(session, case_id, "customer", text, result.facts)
        session.commit()  # customer turn is stored before the reply (ordering by time)
        return self._respond(session, result)

    def confirm(
        self,
        session: Session,
        customer_id: uuid.UUID,
        case_id: uuid.UUID,
        accept: bool,
        option: str | None,
        refund_method: str | None = None,
        exchange_sku: str | None = None,
    ) -> CaseView:
        self._owned(session, customer_id, case_id)
        payload: dict[str, Any] = {"accept": accept}
        for key, value in (
            ("option", option),
            ("refund_method", refund_method),
            ("exchange_sku", exchange_sku),
        ):
            if value:
                payload[key] = value
        result = self.runner.dispatch(
            case_id, Event("customer_confirm", payload, "customer", str(customer_id))
        )
        return self._respond(session, result)

    def upload(
        self, session: Session, customer_id: uuid.UUID, case_id: uuid.UUID, uploads: list[Upload]
    ) -> CaseView:
        """Evidence photos: validated, checked and stored, then assessed and handed to the case."""
        self._owned(session, customer_id, case_id)
        case = session.get(ReturnCase, case_id)
        assert case is not None
        spec = self.runner.registry.spec(case.graph_version)
        if case.status == "closed" or spec.node(case.current_node).waits_for != "customer_upload":
            raise StaleEvent("this case is not waiting for photos")
        order = session.get(Order, case.order_id)
        rows, checks = ingest(
            session, self.store, case, uploads, order.delivered_at if order else None
        )
        vision: dict[str, Any] | None = None
        refs: list[str] = []
        if self.llm is not None:
            facts = self.runner.facts(case_id)
            vision, refs = vision_assessment(session, self.store, self.llm, case_id, facts, rows)
        session.commit()  # evidence is kept even if the case moved on meanwhile
        payload = {
            "files": [str(r.id) for r in rows],
            "checks": checks,
            "vision": vision,
            "prompt_refs": refs,
        }
        result = self.runner.dispatch(
            case_id, Event("customer_upload", payload, "customer", str(customer_id))
        )
        return self._respond(session, result)

    def staff_event(self, session: Session, case_id: uuid.UUID, event: Event) -> CaseView:
        """A person's decision (approval, resolution) moves the case; the customer is told."""
        result = self.runner.dispatch(case_id, event)
        return self._respond(session, result)

    def request_review(
        self, session: Session, customer_id: uuid.UUID, case_id: uuid.UUID, reason: str
    ) -> None:
        """The customer asks a person to review an automated denial (once per case)."""
        self._owned(session, customer_id, case_id)
        case = session.get(ReturnCase, case_id)
        assert case is not None
        facts = self.runner.facts(case_id)
        if case.status != "closed" or not facts.get("explanation"):
            raise ReviewNotAvailable("only automatically declined returns can be reviewed")
        if session.scalars(
            select(QueueItem.id).where(QueueItem.case_id == case_id, QueueItem.queue == "review")
        ).first():
            raise ReviewNotAvailable("a review was already requested for this return")
        now = datetime.now(UTC)
        queues.enqueue(session, case_id, "review", reason[:500], queues.priority_for(facts), now)
        self._store(session, case_id, "customer", reason, facts)
        reply_text = "Thanks — a member of our team will review this decision and reply here."
        self._store(session, case_id, "agent", reply_text, facts)
        audit.append(
            session,
            actor_type="customer",
            actor_id=str(customer_id),
            case_id=case_id,
            action="review.requested",
            payload={"reasons": facts.get("explanation")},
        )
        notify(session, case_id, "review_requested", audience="staff")
        session.commit()

    def history(
        self, session: Session, customer_id: uuid.UUID, case_id: uuid.UUID
    ) -> list[dict[str, str]]:
        self._owned(session, customer_id, case_id)
        rows = session.scalars(
            select(Message).where(Message.case_id == case_id).order_by(Message.created_at)
        ).all()
        return [{"role": m.role, "text": m.content} for m in rows]

    def _owned(self, session: Session, customer_id: uuid.UUID, case_id: uuid.UUID) -> None:
        case = session.get(ReturnCase, case_id)
        if case is None or case.customer_id != customer_id:
            raise CaseNotFound("case not found")

    def _respond(self, session: Session, result: RunResult) -> CaseView:
        conversation = [
            {"role": m.role, "text": m.content}
            for m in session.scalars(
                select(Message)
                .where(Message.case_id == result.case_id)
                .order_by(Message.created_at)
            ).all()
        ]
        waiting = result.current_node if result.waiting_for else None
        answer = reply(self.llm, {**result.facts, "conversation": conversation}, waiting)
        self._store(session, result.case_id, "agent", answer.text, result.facts)
        timers.agent_replied(session, result.case_id, datetime.now(UTC))  # response SLAs met
        audit.append(
            session,
            actor_type="ai",
            case_id=result.case_id,
            action="message.sent",
            payload={
                "used_template": answer.used_template,
                "violations": answer.violations,
                "prompt_refs": answer.prompt_refs,
            },
        )
        session.commit()
        quote = result.facts.get("refund_quote") or {}
        return CaseView(
            case_id=str(result.case_id),
            status=result.status,
            current_node=result.current_node,
            waiting_for=result.waiting_for,
            reply=answer.text,
            options=list(result.facts.get("options") or []),
            refund_total_minor=quote.get("total_minor"),
        )

    @staticmethod
    def _store(
        session: Session, case_id: uuid.UUID, role: str, text: str, facts: dict[str, Any]
    ) -> None:
        session.add(
            Message(
                case_id=case_id,
                role=role,
                content=text,
                redacted_content=redactor_for(facts).redact(text),
            )
        )
        session.flush()
