"""Support chat before a return exists: the model talks with the customer about their own
orders until it knows the item and the problem, then the return is opened with the agent
(policy, risk, routing and replies all run in the graph from there)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from returns_agent.agent.cases import CaseService, CaseView, _pii
from returns_agent.agent.context import conversation_messages, redactor_for, system_message
from returns_agent.db.models import Customer, Order, Product
from returns_agent.llm.client import LLMRequest, complete_structured
from returns_agent.llm.prompts import load_prompt


class AssistantUnavailable(Exception):
    """No model is configured, so the assistant cannot chat."""


class SupportTurn(BaseModel):
    reply: str
    ready: bool = False
    order_id: str | None = None
    item_id: str | None = None
    reason_category: (
        Literal[
            "size_fit",
            "damaged",
            "defective",
            "wrong_item",
            "not_as_described",
            "changed_mind",
            "other",
        ]
        | None
    ) = None
    desired_resolution: Literal["refund", "exchange", "replacement", "store_credit"] | None = None
    wants_human: bool = False


@dataclass(frozen=True)
class SupportReply:
    reply: str
    case: CaseView | None


def _orders(session: Session, customer_id: uuid.UUID) -> list[dict[str, Any]]:
    titles = dict(session.execute(select(Product.sku, Product.title)).tuples().all())
    now = datetime.now(UTC)
    rows = []
    for order in session.scalars(
        select(Order).options(selectinload(Order.items)).where(Order.customer_id == customer_id)
    ):
        if order.delivered_at is None:
            continue
        for item in order.items:
            rows.append(
                {
                    "order_id": order.external_id,
                    "item_id": item.external_id,
                    "product": titles.get(item.sku, item.sku),
                    "variant": item.sku.split("-", 1)[-1],
                    "qty": item.qty,
                    "days_since_delivery": (now - order.delivered_at).days,
                }
            )
    return rows


def chat(
    session: Session, cases: CaseService, customer_id: uuid.UUID, conversation: list[dict[str, str]]
) -> SupportReply:
    if cases.llm is None:
        raise AssistantUnavailable("the assistant needs a model (LLM_PROVIDER=deepseek)")
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise AssistantUnavailable("account not found")
    orders = _orders(session, customer_id)
    redactor = redactor_for({"pii": _pii(session, customer)})
    request = LLMRequest(
        messages=[
            system_message(load_prompt("support_chat"), {"ORDERS": orders}),
            *conversation_messages(conversation, redactor),
        ],
        max_tokens=600,
    )
    turn = complete_structured(cases.llm, request, SupportTurn)
    reply = redactor.restore(turn.reply)

    known = {(o["order_id"], o["item_id"]) for o in orders}
    if turn.ready and (turn.order_id, turn.item_id) in known:
        said = "\n".join(t["text"] for t in conversation if t.get("role") == "customer")
        view = cases.open(
            session,
            customer_id,
            str(turn.order_id),
            str(turn.item_id),
            1,
            said,
            {
                "reason_category": turn.reason_category,
                "desired_resolution": turn.desired_resolution,
                "wants_human": turn.wants_human or None,
            },
        )
        return SupportReply(reply=reply, case=view)
    return SupportReply(reply=reply, case=None)
