"""Customer case flow: open a case, take messages and confirmations, reply with a verified
message. Every customer and agent message is stored (with a redacted copy) and audited."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from returns_agent.agent.context import redactor_for
from returns_agent.agent.respond import reply
from returns_agent.audit import log as audit
from returns_agent.db.models import (
    Address,
    Customer,
    Message,
    Order,
    OrderItem,
    Product,
    ReturnCase,
)
from returns_agent.graph.runner import CaseRunner, Event, RunResult
from returns_agent.lifecycle import timers
from returns_agent.llm.client import LLMClient
from returns_agent.security.pii import decrypt


class CaseNotFound(Exception):
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
    return {
        "returns_90d": session.scalar(returns) or 0,
        "orders_90d": session.scalar(orders) or 0,
        "account_age_days": customer.account_age_days,
    }


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
    address = session.scalars(select(Address).where(Address.customer_id == customer.id)).first()
    already = 0  # earlier returns of this line: counted from closed cases in Phase 6
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
    def __init__(self, runner: CaseRunner, llm: LLMClient | None) -> None:
        self.runner = runner
        self.llm = llm

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
