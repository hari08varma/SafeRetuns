"""Goodwill: a case-only store-credit gesture, reason-coded and within the granting person's
authority. It is executed through the outbox like any side effect and never touches policy
or the refund totals."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from returns_agent.db.models import Goodwill, Order, ReturnCase, StaffUser
from returns_agent.execution.actions import Intent, enqueue
from returns_agent.hitl.approvals import InvalidRequest, NotAllowed
from returns_agent.lifecycle.notify import notify

REASON_CODES = frozenset({"delay_apology", "service_failure", "courier_issue", "retention"})
CASE_CAP_MINOR = 500_000  # ₹5,000 in total per case, whoever grants it


def grant(
    session: Session,
    case: ReturnCase,
    staff: StaffUser,
    amount_minor: int,
    reason_code: str,
    note: str,
    now: datetime,
) -> Goodwill:
    if staff.status != "active":
        raise NotAllowed("inactive staff cannot grant goodwill")
    if reason_code not in REASON_CODES:
        raise InvalidRequest(f"reason code {reason_code!r} is not valid for goodwill")
    if amount_minor <= 0:
        raise InvalidRequest("amount must be positive")
    if amount_minor > staff.authority_limit_minor:
        raise NotAllowed(f"₹{amount_minor / 100:,.0f} is above your goodwill limit")
    granted = session.scalar(
        select(func.coalesce(func.sum(Goodwill.amount_minor), 0)).where(
            Goodwill.case_id == case.id, Goodwill.status != "failed"
        )
    )
    if int(granted or 0) + amount_minor > CASE_CAP_MINOR:
        raise NotAllowed(f"goodwill for one case is capped at ₹{CASE_CAP_MINOR / 100:,.0f}")
    row = Goodwill(
        case_id=case.id,
        amount_minor=amount_minor,
        reason_code=reason_code,
        note=note,
        granted_by=staff.id,
        status="pending",
    )
    session.add(row)
    session.flush()
    order = session.get(Order, case.order_id)
    enqueue(
        session,
        case.id,
        Intent(
            "goodwill_credit",
            f"{case.id}:goodwill:{row.id}",
            {
                "goodwill_id": str(row.id),
                "order_id": order.external_id if order else "",
                "amount_minor": amount_minor,
            },
        ),
    )
    notify(
        session,
        case.id,
        "goodwill_granted",
        data={"amount_minor": amount_minor},
        key_suffix=f":{row.id}",
    )
    return row


def goodwill_id(payload: dict[str, object]) -> uuid.UUID:
    return uuid.UUID(str(payload["goodwill_id"]))
