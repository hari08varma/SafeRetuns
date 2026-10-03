import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from returns_agent.audit import log as audit
from tests.conftest import needs_db

pytestmark = [needs_db]


def _write(db: Session, case_id: uuid.UUID | None, n: int) -> list[audit.AuditEvent]:
    events = [
        audit.append(
            db,
            actor_type="ai",
            action=f"step.{i}",
            case_id=case_id,
            payload={"i": i, "amount_minor": 129900},
        )
        for i in range(n)
    ]
    db.commit()
    return events


def test_chain_verifies(db: Session) -> None:
    case = uuid.uuid4()
    events = _write(db, case, 4)
    assert events[0].prev_hash == audit.GENESIS
    assert all(b.prev_hash == a.hash for a, b in zip(events, events[1:], strict=False))
    assert audit.verify(db, case) == audit.ChainCheck(ok=True, events=4)


def test_tampered_payload_is_detected(db: Session) -> None:
    case = uuid.uuid4()
    events = _write(db, case, 4)
    db.execute(
        text(
            "UPDATE audit_event SET payload = jsonb_set(payload, '{amount_minor}', "
            "'1') WHERE id = :id"
        ),
        {"id": events[2].id},
    )
    db.commit()
    db.expire_all()
    check = audit.verify(db, case)
    assert not check.ok and check.broken_at == events[2].id


def test_deleted_event_is_detected(db: Session) -> None:
    case = uuid.uuid4()
    events = _write(db, case, 4)
    db.execute(text("DELETE FROM audit_event WHERE id = :id"), {"id": events[1].id})
    db.commit()
    check = audit.verify(db, case)
    assert not check.ok and check.broken_at == events[2].id


def test_chains_are_independent(db: Session) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    _write(db, a, 2)
    _write(db, b, 3)
    _write(db, None, 1)  # global chain
    assert audit.verify(db, a).events == 2 and audit.verify(db, a).ok
    assert audit.verify(db, b).events == 3 and audit.verify(db, b).ok
    assert audit.verify(db).events == 1 and audit.verify(db).ok
