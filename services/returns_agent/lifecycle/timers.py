"""SLA clocks, reminders and inactivity timeouts.

Timers are rows created in the same transaction as the transition that starts them, and
resolved when the awaited thing happens. `tick` fires due timers: it commits `fired_at`
first and performs the effect afterwards, so it never holds a lock while resuming a case.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.audit import log as audit
from returns_agent.db.models import ReturnCase, SlaTimer
from returns_agent.lifecycle.notify import notify

logger = logging.getLogger(__name__)

# India: grievance acknowledged within 48 hours and resolved within one month.
SLA = {
    "sla:first_response": timedelta(hours=1),
    "sla:grievance_ack": timedelta(hours=48),
    "sla:resolution": timedelta(days=7),
    "sla:grievance_resolution": timedelta(days=30),
}
RESPONSE_SLAS = ("sla:first_response", "sla:grievance_ack")
CUSTOMER_WAIT = {
    "CLARIFY": "customer_message",
    "REQUEST_EVIDENCE": "customer_upload",
    "CUSTOMER_CONFIRM": "customer_confirm",
}
REMINDER_AFTER = timedelta(hours=24)
TIMEOUT_AFTER = timedelta(days=7)
PICKUP_REMINDER_AFTER = timedelta(hours=24)


def schedule(session: Session, case_id: uuid.UUID, kind: str, due_at: datetime) -> None:
    session.execute(
        insert(SlaTimer)
        .values(id=uuid.uuid4(), case_id=case_id, kind=kind, due_at=due_at)
        .on_conflict_do_nothing(index_elements=["case_id", "kind"])
    )


def resolve(
    session: Session,
    case_id: uuid.UUID,
    now: datetime,
    kinds: tuple[str, ...] | None = None,
    prefix: str | None = None,
) -> None:
    stmt = update(SlaTimer).where(
        SlaTimer.case_id == case_id, SlaTimer.fired_at.is_(None), SlaTimer.resolved_at.is_(None)
    )
    if kinds is not None:
        stmt = stmt.where(SlaTimer.kind.in_(kinds))
    if prefix is not None:
        stmt = stmt.where(SlaTimer.kind.startswith(prefix))
    session.execute(stmt.values(resolved_at=now))


def on_transition(
    session: Session,
    case_id: uuid.UUID,
    new_nodes: list[str],
    waiting_node: str | None,
    visit: int,
    now: datetime,
) -> None:
    if "START" in new_nodes:
        for kind, after in SLA.items():
            schedule(session, case_id, kind, now + after)
    if new_nodes:
        resolve(session, case_id, now, prefix="customer_")  # the customer step is over
    if new_nodes and waiting_node in CUSTOMER_WAIT:
        schedule(
            session, case_id, f"customer_reminder:{waiting_node}:{visit}", now + REMINDER_AFTER
        )
        schedule(session, case_id, f"customer_timeout:{waiting_node}:{visit}", now + TIMEOUT_AFTER)
    if waiting_node == "TRACK_SHIPMENT" and "SCHEDULE_PICKUP" in new_nodes:
        schedule(session, case_id, "pickup_reminder", now + PICKUP_REMINDER_AFTER)
    if waiting_node is None:
        resolve(session, case_id, now)  # closed: nothing is owed any more


def agent_replied(session: Session, case_id: uuid.UUID, now: datetime) -> None:
    resolve(session, case_id, now, kinds=RESPONSE_SLAS)


TimeoutDispatcher = Callable[[uuid.UUID, str, str], None]  # (case_id, node, event type)


def tick(
    sessions: sessionmaker[Session],
    dispatch_timeout: TimeoutDispatcher,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    now = now or datetime.now(UTC)
    fired = 0
    for _ in range(limit):
        with sessions.begin() as session:
            timer = session.scalars(
                select(SlaTimer)
                .where(
                    SlaTimer.fired_at.is_(None),
                    SlaTimer.resolved_at.is_(None),
                    SlaTimer.due_at <= now,
                )
                .order_by(SlaTimer.due_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if timer is None:
                break
            timer.fired_at = now
            case_id, kind = timer.case_id, timer.kind
        fired += 1
        try:
            _fire(sessions, dispatch_timeout, case_id, kind, now)
        except Exception:  # one bad timer must not stop the others
            logger.exception("timer %s for case %s failed", kind, case_id)
    return fired


def _fire(
    sessions: sessionmaker[Session],
    dispatch_timeout: TimeoutDispatcher,
    case_id: uuid.UUID,
    kind: str,
    now: datetime,
) -> None:
    if kind.startswith("customer_timeout:"):
        node = kind.split(":")[1]
        dispatch_timeout(case_id, node, CUSTOMER_WAIT[node])
        return
    with sessions.begin() as session:
        if kind.startswith("customer_reminder:"):
            notify(
                session,
                case_id,
                "reminder",
                data={"step": kind.split(":")[1]},
                key_suffix=f":{kind}",
            )
        elif kind == "pickup_reminder":
            notify(session, case_id, "pickup_reminder")
        elif kind.startswith("sla:") or kind == "refund_tat":
            case = session.get(ReturnCase, case_id)
            if case is not None and case.status != "closed":
                case.priority += 1  # moves up the supervisors' queue
                audit.append(
                    session,
                    actor_type="system",
                    case_id=case_id,
                    action="sla.breached",
                    payload={"sla": kind},
                )
                notify(
                    session,
                    case_id,
                    "sla_breached",
                    audience="staff",
                    data={"sla": kind},
                    key_suffix=f":{kind}",
                )
