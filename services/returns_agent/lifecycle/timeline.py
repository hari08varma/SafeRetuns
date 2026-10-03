"""One merged, time-ordered view of a case. Customers see messages, notifications and
milestones; staff also see every audit event and the decision records."""

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.db.models import AuditEvent, DecisionRecord, Message, Notification

MILESTONES = {
    "CHECK_ELIGIBILITY": "Eligibility checked",
    "CREATE_EXCHANGE": "Exchange created",
    "CREATE_REPLACEMENT": "Replacement created",
    "SCHEDULE_PICKUP": "Pickup booked",
    "INSPECT_QC": "Quality check done",
    "ISSUE_REFUND": "Refund processed",
    "KEEP_ITEM_REFUND": "Refund processed",
    "ESCALATE": "Handled by a specialist",
    "CLOSE": "Case closed",
}


def build_timeline(
    session: Session, case_id: uuid.UUID, audience: Literal["customer", "staff"]
) -> list[dict[str, Any]]:
    entries: list[tuple[datetime, dict[str, Any]]] = []
    for m in session.scalars(select(Message).where(Message.case_id == case_id)):
        entries.append((m.created_at, {"type": "message", "role": m.role, "text": m.content}))
    for n in session.scalars(select(Notification).where(Notification.case_id == case_id)):
        if audience == "customer" and n.audience != "customer":
            continue
        entries.append(
            (
                n.created_at,
                {
                    "type": "notification",
                    "template": n.template,
                    "status": n.status,
                    "audience": n.audience,
                },
            )
        )
    for e in session.scalars(select(AuditEvent).where(AuditEvent.case_id == case_id)):
        node = e.payload.get("node") if e.action == "graph.node" else None
        if audience == "customer":
            if node in MILESTONES:
                entries.append((e.created_at, {"type": "milestone", "label": MILESTONES[node]}))
            continue
        entries.append(
            (
                e.created_at,
                {"type": "audit", "action": e.action, "actor": e.actor_type, "payload": e.payload},
            )
        )
    if audience == "staff":
        for d in session.scalars(select(DecisionRecord).where(DecisionRecord.case_id == case_id)):
            entries.append((d.created_at, {"type": "decision", "record": d.record}))
    entries.sort(key=lambda item: item[0])
    return [{"at": at.isoformat(), **entry} for at, entry in entries]
