"""Milestone notifications. Each is a Notification row plus an outbox action, both keyed so
a milestone is announced exactly once. Content comes from decided facts only; contact
details are looked up at send time, so no PII sits in the outbox."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from returns_agent.db.models import Notification
from returns_agent.execution.actions import Intent, enqueue

CUSTOMER_CHANNEL = "sms"
STAFF_CHANNEL = "staff"


def notify(
    session: Session,
    case_id: uuid.UUID,
    template: str,
    *,
    audience: str = "customer",
    data: dict[str, Any] | None = None,
    key_suffix: str = "",
) -> bool:
    key = f"{case_id}:notify:{audience}:{template}{key_suffix}"
    notification = Notification(
        case_id=case_id,
        audience=audience,
        template=template,
        channel=CUSTOMER_CHANNEL if audience == "customer" else STAFF_CHANNEL,
        data=data or {},
    )
    session.add(notification)
    session.flush()
    if not enqueue(
        session,
        case_id,
        Intent("send_notification", key, {"notification_id": str(notification.id)}),
    ):
        session.delete(notification)  # already announced
        session.flush()
        return False
    return True


def on_transition(
    session: Session,
    case_id: uuid.UUID,
    new_nodes: list[str],
    waiting_node: str | None,
    fallback: str | None,
    facts: dict[str, Any],
) -> None:
    if "SCHEDULE_PICKUP" in new_nodes and (facts.get("pickup") or {}).get("scheduled"):
        pickup = facts["pickup"]
        notify(
            session,
            case_id,
            "pickup_scheduled",
            data={"awb": pickup.get("awb"), "slot": pickup.get("slot")},
        )
    if waiting_node == "INSPECT_QC" and "TRACK_SHIPMENT" in new_nodes:
        notify(session, case_id, "item_received")
    if facts.get("refund_issued") and {"ISSUE_REFUND", "KEEP_ITEM_REFUND"} & set(new_nodes):
        refund = facts.get("refund") or {}
        notify(
            session,
            case_id,
            "refund_issued",
            data={"amount_minor": refund.get("amount_minor"), "method": refund.get("method")},
        )
    if waiting_node == "HUMAN_APPROVAL":
        notify(session, case_id, "under_review")
        notify(session, case_id, "approval_needed", audience="staff")
    if waiting_node is not None and waiting_node == fallback:
        notify(session, case_id, "escalated")
        notify(session, case_id, "case_escalated", audience="staff")
    if "CLOSE" in new_nodes:
        notify(session, case_id, "case_closed", data={"outcome": facts.get("close_outcome")})
