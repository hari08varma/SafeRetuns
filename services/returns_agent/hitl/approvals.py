"""Approvals: reason-coded sign-offs within authority limits, two people above the limit, and
a signed single-use token that execution must consume before releasing money or goods
(INV-3, enforced again in the relay)."""

import hashlib
import hmac
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.config import get_settings
from returns_agent.db.models import Approval, DecisionRecord, StaffUser
from returns_agent.decision.risk import item_value_minor
from returns_agent.security.tokens import Role

TWO_PERSON_ABOVE_MINOR = 2_500_000  # ₹25,000: above this two different approvers sign off
APPROVER_ROLES = frozenset({Role.APPROVER, Role.ADMIN})
REASON_CODES: dict[str, frozenset[str]] = {
    "approve": frozenset(
        {"policy_compliant", "evidence_sufficient", "verified_with_customer", "low_risk_on_review"}
    ),
    "modify": frozenset({"better_resolution", "partial_resolution", "customer_preference"}),
    "reject": frozenset(
        {"insufficient_evidence", "suspected_fraud", "outside_policy", "duplicate_claim"}
    ),
}
MONEY_OPTIONS = frozenset({"refund", "store_credit", "keep_item_refund"})
# The outbox action that releases value for each resolution.
RELEASING_ACTION = {
    "refund": "refund",
    "store_credit": "refund",
    "keep_item_refund": "refund",
    "exchange": "create_exchange",
    "replacement": "create_replacement",
}
OPEN = ("pending", "partial")


class ApprovalError(Exception):
    """Base: the message is safe to show staff."""


class NotAllowed(ApprovalError):
    pass  # role or authority


class Conflict(ApprovalError):
    pass  # state: not pending, same person twice, sign-offs disagree


class InvalidRequest(ApprovalError):
    pass  # reason code or option


class TokenInvalid(Exception):
    """Execution refused: no valid, unused approval for this action."""


def amount_for(facts: dict[str, Any], option: str) -> int:
    """Value released by `option`: the refund for money options, the item for goods."""
    quote = facts.get("refund_quote") or {}
    if option in MONEY_OPTIONS and quote.get("total_minor"):
        return int(quote["total_minor"])
    return item_value_minor(facts)


def open_approval(
    session: Session, case_id: uuid.UUID, decision: DecisionRecord, facts: dict[str, Any]
) -> Approval:
    existing = session.scalars(
        select(Approval).where(Approval.case_id == case_id, Approval.status.in_(OPEN))
    ).first()
    if existing is not None:
        return existing
    option = str(facts.get("chosen_option"))
    amount = amount_for(facts, option)
    approval = Approval(
        case_id=case_id,
        decision_id=decision.id,
        requested_action=option,
        amount_minor=amount,
        required_approvals=2 if amount > TWO_PERSON_ABOVE_MINOR else 1,
        status="pending",
    )
    session.add(approval)
    session.flush()
    return approval


def decide(
    approval: Approval,
    staff: StaffUser,
    decision: str,
    reason_code: str,
    facts: dict[str, Any],
    now: datetime,
    option: str | None = None,
    note: str = "",
) -> str | None:
    """Records one sign-off. Returns the execution token once fully approved."""
    if approval.status not in OPEN:
        raise Conflict(f"approval is already {approval.status}")
    if staff.status != "active" or Role(staff.role) not in APPROVER_ROLES:
        raise NotAllowed("only active approvers can decide approvals")
    if reason_code not in REASON_CODES.get(decision, frozenset()):
        raise InvalidRequest(f"reason code {reason_code!r} is not valid for {decision!r}")
    if any(s["staff_id"] == str(staff.id) for s in approval.signoffs):
        raise Conflict("the second sign-off must come from a different person")

    chosen = approval.requested_action
    if decision == "modify":
        if option not in (facts.get("options") or []) or option == chosen:
            raise InvalidRequest("choose a different option that was offered")
        chosen = str(option)
    amount = amount_for(facts, chosen) if decision == "modify" else approval.amount_minor
    if approval.signoffs:  # second of two: must agree with the first
        first = approval.signoffs[0]
        if decision != "reject" and (first["decision"], first["option"]) != (decision, chosen):
            raise Conflict("the second sign-off must match the first or reject")
    if decision != "reject":
        two_person = amount > TWO_PERSON_ABOVE_MINOR
        if not two_person and amount > staff.authority_limit_minor:
            raise NotAllowed(f"₹{amount / 100:,.0f} is above your approval limit")
        approval.required_approvals = 2 if two_person else 1

    approval.signoffs = [
        *approval.signoffs,
        {
            "staff_id": str(staff.id),
            "role": staff.role,
            "decision": decision,
            "option": chosen,
            "reason_code": reason_code,
            "note": note,
            "at": now.isoformat(),
        },
    ]
    approval.reason_code = reason_code
    if decision == "reject":
        approval.status, approval.decided_at = "rejected", now
        return None
    approval.requested_action, approval.amount_minor = chosen, amount
    if len(approval.signoffs) < approval.required_approvals:
        approval.status = "partial"
        return None
    approval.status, approval.decided_at = "approved", now
    approval.approver_id = staff.id
    token = _sign(approval)
    approval.token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token


def _key() -> bytes:
    return hmac.new(get_settings().jwt_secret.encode(), b"approval-token", "sha256").digest()


def _mac(approval: Approval) -> str:
    bound = f"{approval.id}|{approval.case_id}|{approval.requested_action}|{approval.amount_minor}"
    return hmac.new(_key(), bound.encode(), hashlib.sha256).hexdigest()


def _sign(approval: Approval) -> str:
    return f"{approval.id}.{_mac(approval)}"


def consume(
    session: Session, case_id: uuid.UUID, token: str | None, action: str, amount: int, now: datetime
) -> None:
    """Verifies the token for this case, action and amount, and marks it used (once)."""
    try:
        approval_id = uuid.UUID(str(token).split(".", 1)[0])
    except ValueError as exc:
        raise TokenInvalid("missing or malformed approval token") from exc
    approval = session.get(Approval, approval_id, with_for_update=True)
    if (
        approval is None
        or approval.case_id != case_id
        or approval.status != "approved"
        or approval.token_hash != hashlib.sha256(str(token).encode()).hexdigest()
        or not hmac.compare_digest(str(token), _sign(approval))
    ):
        raise TokenInvalid("approval token does not match an approved decision")
    if approval.consumed_at is not None:
        raise TokenInvalid("approval token was already used")
    if RELEASING_ACTION.get(approval.requested_action) != action:
        raise TokenInvalid(f"approval covers {approval.requested_action}, not {action}")
    if action == "refund" and amount > approval.amount_minor:
        raise TokenInvalid("refund is larger than the approved amount")
    approval.consumed_at = now
