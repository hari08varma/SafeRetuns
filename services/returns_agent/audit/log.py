"""Append-only, hash-chained audit log. One chain per case; 'global' for other events.

Each event's hash covers its content and the previous event's hash, so editing or
deleting any row breaks verification from that row onward.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from returns_agent.db.models import AuditEvent

GENESIS = "0" * 64
GLOBAL_CHAIN = "global"


def _digest(event: AuditEvent) -> str:
    body = {
        "chain_key": event.chain_key,
        "case_id": str(event.case_id) if event.case_id else None,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "action": event.action,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
        "prev_hash": event.prev_hash,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def append(
    session: Session,
    *,
    actor_type: str,
    action: str,
    actor_id: str | None = None,
    case_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an event to the caller's transaction (committed with the business change)."""
    chain_key = str(case_id) if case_id else GLOBAL_CHAIN
    # Serialise writers per chain for the rest of this transaction.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": chain_key})
    prev = session.scalar(
        select(AuditEvent.hash)
        .where(AuditEvent.chain_key == chain_key)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    )
    event = AuditEvent(
        chain_key=chain_key,
        case_id=case_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        payload=payload or {},
        created_at=datetime.now(UTC),
        prev_hash=prev or GENESIS,
    )
    event.hash = _digest(event)
    session.add(event)
    session.flush()
    return event


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    events: int
    broken_at: uuid.UUID | None = None


def verify(session: Session, case_id: uuid.UUID | None = None) -> ChainCheck:
    chain_key = str(case_id) if case_id else GLOBAL_CHAIN
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.chain_key == chain_key).order_by(AuditEvent.seq)
    ).all()
    prev = GENESIS
    for event in events:
        if event.prev_hash != prev or _digest(event) != event.hash:
            return ChainCheck(ok=False, events=len(events), broken_at=event.id)
        prev = event.hash
    return ChainCheck(ok=True, events=len(events))
