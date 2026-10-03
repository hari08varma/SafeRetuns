"""Work queues for people, with priorities, assignment and SLA clocks. A breached item is
raised to supervisors automatically."""

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.audit import log as audit
from returns_agent.db.models import DecisionRecord, QueueItem
from returns_agent.decision.risk import item_value_minor
from returns_agent.hitl.approvals import open_approval
from returns_agent.lifecycle.notify import notify

SLA = {
    "approval": timedelta(hours=4),
    "escalation": timedelta(hours=8),
    "fraud_review": timedelta(hours=24),
    "dispute": timedelta(hours=24),
    "review": timedelta(hours=48),  # customer asked for a person to review a denial
}
QUEUES = tuple(SLA)
# Signals that send an escalation to the fraud-review queue instead of general escalation.
FRAUD_SIGNALS = frozenset(
    {
        "duplicate_photo_other_customer",
        "catalogue_photo",
        "prior_confirmed_fraud",
        "serial_returner",
        "ai_generated_marker",
        "linked_accounts",
    }
)
BREACH_BUMP = 100


def priority_for(facts: dict[str, Any]) -> int:
    """Higher first: risk, then value (₹1,000 steps, capped)."""
    risk = float((facts.get("risk") or {}).get("score", 0.0))
    return round(risk * 50) + min(50, item_value_minor(facts) // 100_000)


def enqueue(
    session: Session, case_id: uuid.UUID, queue: str, reason: str, priority: int, now: datetime
) -> QueueItem:
    existing = session.scalars(
        select(QueueItem).where(
            QueueItem.case_id == case_id, QueueItem.queue == queue, QueueItem.status != "done"
        )
    ).first()
    if existing is not None:
        return existing
    item = QueueItem(
        case_id=case_id,
        queue=queue,
        reason=reason,
        priority=priority,
        status="open",
        due_at=now + SLA[queue],
    )
    session.add(item)
    session.flush()
    return item


def on_transition(
    session: Session,
    case_id: uuid.UUID,
    new_nodes: list[str],
    waiting_node: str | None,
    fallback: str | None,
    facts: dict[str, Any],
    decision: DecisionRecord | None,
    now: datetime,
) -> None:
    """Called by the runner in the transition's transaction when a case reaches a person."""
    if waiting_node is None or not new_nodes:
        return  # the case did not move; items and approvals are idempotent anyway
    decision_row = (
        decision
        or session.scalars(
            select(DecisionRecord)
            .where(DecisionRecord.case_id == case_id)
            .order_by(DecisionRecord.created_at.desc())
        ).first()
    )
    priority = priority_for(facts)
    rationale = (facts.get("decision") or {}).get("rationale", "")
    if waiting_node == "HUMAN_APPROVAL" and decision_row is not None:
        open_approval(session, case_id, decision_row, facts)
        enqueue(session, case_id, "approval", rationale, priority, now)
    elif waiting_node == fallback:
        signals = set((facts.get("risk") or {}).get("signals", []))
        queue = "fraud_review" if signals & FRAUD_SIGNALS else "escalation"
        reason = rationale or "the automated flow could not continue"
        enqueue(session, case_id, queue, reason, priority, now)
    elif waiting_node == "DISPUTE":
        enqueue(session, case_id, "dispute", "quality check did not pass", priority, now)


def close(session: Session, case_id: uuid.UUID, queue: str, outcome: str, now: datetime) -> None:
    for item in session.scalars(
        select(QueueItem).where(
            QueueItem.case_id == case_id, QueueItem.queue == queue, QueueItem.status != "done"
        )
    ):
        item.status, item.outcome, item.closed_at = "done", outcome, now


def list_open(session: Session, queue: str) -> list[QueueItem]:
    return list(
        session.scalars(
            select(QueueItem)
            .where(QueueItem.queue == queue, QueueItem.status != "done")
            .order_by(QueueItem.priority.desc(), QueueItem.due_at)
        )
    )


def tick(sessions: Callable[[], Session], now: datetime) -> int:
    """Raise overdue items to supervisors (once per item)."""
    with sessions() as session:
        overdue = session.scalars(
            select(QueueItem)
            .where(
                QueueItem.status != "done",
                QueueItem.due_at <= now,
                QueueItem.escalated_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for item in overdue:
            item.escalated_at = now
            item.priority += BREACH_BUMP
            audit.append(
                session,
                actor_type="system",
                case_id=item.case_id,
                action="queue.sla_breached",
                payload={"queue": item.queue, "item": str(item.id)},
            )
            notify(
                session,
                item.case_id,
                "queue_sla_breached",
                audience="supervisor",
                data={"queue": item.queue},
                key_suffix=f":{item.id}",
            )
        session.commit()
        return len(overdue)
