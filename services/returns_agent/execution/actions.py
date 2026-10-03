"""Side-effect intents derived from where a case is waiting. Each intent has an
idempotency key so it can be queued and performed any number of times with one effect."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from returns_agent.db.models import Outbox

ACTION_NODES = frozenset(
    {"SCHEDULE_PICKUP", "CREATE_EXCHANGE", "CREATE_REPLACEMENT", "ISSUE_REFUND", "KEEP_ITEM_REFUND"}
)
# Actions whose result resumes a waiting node; the rest are fire-and-forget.
RESUMING_ACTIONS = frozenset({"schedule_pickup", "create_exchange", "create_replacement", "refund"})


@dataclass(frozen=True)
class Intent:
    action: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)


def refund_destination(facts: dict[str, Any]) -> str:
    if facts.get("chosen_option") == "store_credit":
        return "store_credit"
    if facts.get("refund_method"):
        return str(facts["refund_method"])
    cod = (facts.get("order") or {}).get("payment_method") == "cod"
    return "store_credit" if cod else "source"  # cash cannot go back to its source


def build_action(node: str, facts: dict[str, Any], case_id: uuid.UUID) -> Intent | None:
    item = facts.get("item") or {}
    order = facts.get("order") or {}
    item_ref = str(item.get("item_id") or item.get("sku"))
    base = f"{case_id}:{item_ref}"
    if node == "SCHEDULE_PICKUP":
        return Intent("schedule_pickup", f"{base}:pickup", {"pincode": facts.get("pincode")})
    if node == "CREATE_EXCHANGE":
        to_sku = (facts.get("request") or {}).get("exchange_sku") or item.get("sku")
        return Intent(
            "create_exchange",
            f"{base}:exchange",
            {
                "order_id": order.get("order_id"),
                "item_id": item_ref,
                "from_sku": item.get("sku"),
                "to_sku": to_sku,
                "qty": item.get("qty_returning", 1),
            },
        )
    if node == "CREATE_REPLACEMENT":
        return Intent(
            "create_replacement",
            f"{base}:replacement",
            {"order_id": order.get("order_id"), "item_id": item_ref},
        )
    if node in ("ISSUE_REFUND", "KEEP_ITEM_REFUND"):
        quote = facts.get("refund_quote") or {}
        # One refund per item, whichever node issues it (INV-5 at the payment level too).
        return Intent(
            "refund",
            f"{base}:refund",
            {
                "order_id": order.get("order_id"),
                "amount_minor": quote.get("total_minor"),
                "method": refund_destination(facts),
            },
        )
    return None


def compensation_intents(facts: dict[str, Any], case_id: uuid.UUID) -> list[Intent]:
    """Undo work done ahead of a return that can no longer be collected."""
    item_ref = str((facts.get("item") or {}).get("item_id") or (facts.get("item") or {}).get("sku"))
    base = f"{case_id}:{item_ref}"
    intents = []
    exchange = facts.get("exchange") or {}
    if exchange.get("status") == "created":
        intents.append(
            Intent(
                "cancel_exchange",
                f"{base}:cancel_exchange",
                {
                    "new_order_ref": exchange.get("new_order_ref"),
                    "reservation_id": exchange.get("reservation_id"),
                },
            )
        )
    replacement = facts.get("replacement") or {}
    if replacement.get("status") == "created":
        intents.append(
            Intent(
                "cancel_replacement",
                f"{base}:cancel_replacement",
                {"new_order_ref": replacement.get("new_order_ref")},
            )
        )
    return intents


def enqueue(session: Session, case_id: uuid.UUID, intent: Intent) -> bool:
    """Queue in the caller's transaction. Returns False if this intent was already queued."""
    result = session.execute(
        insert(Outbox)
        .values(
            id=uuid.uuid4(),
            case_id=case_id,
            action=intent.action,
            payload=intent.payload,
            idempotency_key=intent.idempotency_key,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Outbox.id)
    )
    return result.first() is not None
