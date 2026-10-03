"""Handoff packet: everything a person needs to decide without re-investigating — a case
summary written from the decision record, the timeline, risk signals, evidence, the policy
trace and the suggested action with its alternatives. Contact details stay masked."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.db.models import (
    Approval,
    Customer,
    DecisionRecord,
    Evidence,
    Goodwill,
    QueueItem,
    ReturnCase,
)
from returns_agent.graph.nodes import clause_texts
from returns_agent.lifecycle.timeline import build_timeline
from returns_agent.security.pii import decrypt, mask_email, mask_phone


def summary(facts: dict[str, Any], record: dict[str, Any] | None) -> str:
    item = facts.get("item") or {}
    request = facts.get("request") or {}
    policy = facts.get("policy") or {}
    stats = facts.get("customer_stats") or {}
    parts = [
        f"Customer wants {request.get('desired_resolution') or 'a resolution'} for "
        f"{item.get('sku')} ({item.get('category')}), reason: "
        f"{(request.get('reason_category') or 'not given').replace('_', ' ')}.",
        f"Policy: {'eligible' if policy.get('eligible') else 'not eligible'}"
        + (
            f", legal protection {', '.join(policy['legal_protection'])}"
            if policy.get("legal_protection")
            else ""
        )
        + ".",
        f"History: {stats.get('returns_90d', 0)} returns / {stats.get('orders_90d', 0)} orders "
        f"in 90 days, account {stats.get('account_age_days', '?')} days old.",
    ]
    evidence = facts.get("evidence")
    if evidence:
        flags = ", ".join(evidence.get("signals") or []) or "no flags"
        parts.append(
            f"Evidence: confidence {evidence.get('confidence')}, {flags}"
            + (f", grade {evidence['condition_grade']}" if evidence.get("condition_grade") else "")
            + "."
        )
    if record:
        parts.append(record.get("rationale", ""))
    return " ".join(p for p in parts if p)


def packet(session: Session, case_id: uuid.UUID, facts: dict[str, Any]) -> dict[str, Any]:
    case = session.get(ReturnCase, case_id)
    if case is None:
        raise KeyError(case_id)
    customer = session.get(Customer, case.customer_id)
    decision = session.scalars(
        select(DecisionRecord)
        .where(DecisionRecord.case_id == case_id)
        .order_by(DecisionRecord.created_at.desc())
    ).first()
    record = decision.record if decision else None
    policy = facts.get("policy") or {}
    texts = clause_texts()
    approval = session.scalars(
        select(Approval).where(Approval.case_id == case_id).order_by(Approval.created_at.desc())
    ).first()
    return {
        "case": {
            "id": str(case.id),
            "status": case.status,
            "current_node": case.current_node,
            "route": case.route,
            "priority": case.priority,
            "graph_version": case.graph_version,
        },
        "customer": {
            "phone": mask_phone(decrypt(customer.phone_enc)) if customer else None,
            "email": mask_email(decrypt(customer.email_enc)) if customer else None,
            "tier": customer.tier if customer else None,
            "stats": facts.get("customer_stats") or {},
        },
        "summary": summary(facts, record),
        "suggested": {
            "option": facts.get("chosen_option"),
            "alternatives": facts.get("scored_options") or [],
            "pruned": facts.get("pruned_options") or {},
            "refund_quote": facts.get("refund_quote"),
        },
        "risk": facts.get("risk") or {},
        "evidence": {
            "assessment": facts.get("evidence"),
            "files": [
                {
                    "id": str(e.id),
                    "thumbnail": e.thumb_uri,
                    "signals": (e.checks or {}).get("signals", []),
                    "assessment": e.assessment or None,
                }
                for e in session.scalars(select(Evidence).where(Evidence.case_id == case_id))
            ],
        },
        "policy": {
            "eligible": policy.get("eligible"),
            "clauses": [
                {
                    "clause_id": t["clause_id"],
                    "result": t["result"],
                    "text": texts.get(t["clause_id"]),
                }
                for t in policy.get("trace", [])
                if t.get("result") != "not_applicable"
            ],
        },
        "decision": record,
        "approval": None
        if approval is None
        else {
            "id": str(approval.id),
            "status": approval.status,
            "action": approval.requested_action,
            "amount_minor": approval.amount_minor,
            "required_approvals": approval.required_approvals,
            "signoffs": approval.signoffs,
        },
        "goodwill": [
            {"amount_minor": g.amount_minor, "reason_code": g.reason_code, "status": g.status}
            for g in session.scalars(select(Goodwill).where(Goodwill.case_id == case_id))
        ],
        "queue": [
            {"queue": q.queue, "status": q.status, "due_at": q.due_at.isoformat()}
            for q in session.scalars(select(QueueItem).where(QueueItem.case_id == case_id))
        ],
        "timeline": build_timeline(session, case_id, "staff"),
    }
