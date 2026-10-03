"""Public knowledge graph data: what the agent decided, through which steps, and why.

Anonymised by construction: only case ids (shortened), products, steps, decisions and
reasons. No names, phone numbers, addresses or customer messages."""

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.config import config_dir
from returns_agent.db.models import (
    AuditEvent,
    DecisionRecord,
    OrderItem,
    Product,
    QueueItem,
    ReturnCase,
    ReturnItem,
)

HUMAN_ACTORS = ("staff",)


@lru_cache
def clause_texts() -> dict[str, str]:
    texts: dict[str, str] = {}
    for path in sorted((config_dir() / "policies").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for rule in doc.get("rules", []):
            if rule.get("clause_id"):
                texts[rule["clause_id"]] = " ".join(str(rule.get("text", "")).split())
    return texts


def _steps(events: list[AuditEvent]) -> list[str]:
    steps: list[str] = []
    for e in events:
        node = (e.payload or {}).get("node")
        if e.action == "graph.node" and node and (not steps or steps[-1] != node):
            steps.append(node)
    return steps


def _with_current(steps: list[str], current: str | None) -> list[str]:
    """The node a case is waiting at has not run yet, so it is not in the audit trail."""
    return [*steps, current] if current and (not steps or steps[-1] != current) else steps


def _overall(conf: Any) -> float | None:
    return conf.get("overall") if isinstance(conf, dict) else conf


def knowledge_graph(session: Session, limit: int = 40) -> dict[str, Any]:
    cases = session.scalars(
        select(ReturnCase).order_by(ReturnCase.created_at.desc()).limit(limit)
    ).all()
    ids = [c.id for c in cases]
    events: dict[Any, list[AuditEvent]] = {i: [] for i in ids}
    for e in session.scalars(
        select(AuditEvent).where(AuditEvent.case_id.in_(ids)).order_by(AuditEvent.seq)
    ):
        events[e.case_id].append(e)
    decisions = {
        d.case_id: d.record
        for d in session.scalars(
            select(DecisionRecord)
            .where(DecisionRecord.case_id.in_(ids))
            .order_by(DecisionRecord.created_at)
        )
    }
    queues = {
        q.case_id: q.queue
        for q in session.scalars(select(QueueItem).where(QueueItem.case_id.in_(ids)))
    }
    products = dict(
        session.execute(
            select(ReturnItem.case_id, Product.title)
            .join(OrderItem, OrderItem.id == ReturnItem.order_item_id)
            .join(Product, Product.sku == OrderItem.sku)
            .where(ReturnItem.case_id.in_(ids))
        ).all()
    )

    out = []
    for c in cases:
        record = decisions.get(c.id) or {}
        risk = record.get("risk") or {}
        facts = record.get("facts") or {}
        policy = record.get("policy") or {}
        human = [
            {"action": e.action, "at": e.created_at.isoformat()}
            for e in events[c.id]
            if e.actor_type in HUMAN_ACTORS
        ]
        out.append(
            {
                "id": c.id.hex[:8].upper(),
                "product": products.get(c.id, "Item"),
                "status": c.status,
                "current_node": c.current_node,
                "created_at": c.created_at.isoformat(),
                "steps": _with_current(_steps(events[c.id]), c.current_node),
                "queue": queues.get(c.id),
                "human": human,
                "decision": (
                    {
                        "route": record.get("route") or c.route,
                        "route_reason": record.get("route_reason"),
                        "chosen": record.get("chosen"),
                        "confidence": _overall(record.get("confidence")),
                        "risk_score": risk.get("score"),
                        "signals": risk.get("signals") or [],
                        "eligible": policy.get("eligible"),
                        "clauses": policy.get("applied_clauses") or [],
                        "legal": policy.get("legal_protection") or [],
                        "customer_reason": facts.get("reason"),
                        "desired": facts.get("desired"),
                        "value_minor": facts.get("value_minor"),
                        "rationale": record.get("rationale"),
                    }
                    if record
                    else None
                ),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": out,
        "clauses": clause_texts(),
    }
