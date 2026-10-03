"""Outbox relay: performs queued side effects exactly once in business terms.

For each due row (locked with SKIP LOCKED so several workers can run):
  1. call the adapter with the row's idempotency key and record the domain row;
  2. resume the waiting case with the result (a duplicate resume is rejected as stale);
  3. mark the row done and audit.
A crash anywhere just repeats the steps: the adapter and domain writes are idempotent and
the case ignores a second result. Failures retry with exponential backoff, then resume the
case with ok=False so the graph can compensate or escalate.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.adapters.base import AdapterError
from returns_agent.adapters.bundle import Adapters
from returns_agent.audit import log as audit
from returns_agent.db.models import (
    Customer,
    Exchange,
    Notification,
    Outbox,
    Refund,
    ReplacementOrder,
    ReturnCase,
    Shipment,
)
from returns_agent.execution.actions import RESUMING_ACTIONS
from returns_agent.graph.runner import CaseClosed, CaseRunner, Event, StaleEvent
from returns_agent.lifecycle.timers import schedule
from returns_agent.security.pii import decrypt

logger = logging.getLogger(__name__)

REFUND_TAT = timedelta(days=7)


@dataclass
class Relay:
    engine: Engine
    runner: CaseRunner
    adapters: Adapters
    max_attempts: int = 5
    base_delay_s: int = 30

    def __post_init__(self) -> None:
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def run_once(self, now: datetime | None = None, limit: int = 50) -> int:
        now = now or datetime.now(UTC)
        processed = 0
        for _ in range(limit):
            with self._sessions.begin() as session:
                row = session.scalars(
                    select(Outbox)
                    .where(Outbox.status.in_(("pending", "retry")), Outbox.next_attempt_at <= now)
                    .order_by(Outbox.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                ).first()
                if row is None:
                    break
                self._process(session, row, now)
            processed += 1
        return processed

    def drain(self, now: datetime | None = None, rounds: int = 20) -> int:
        """Process until nothing is due (actions can queue follow-up actions)."""
        total = 0
        for _ in range(rounds):
            done = self.run_once(now)
            total += done
            if done == 0:
                break
        return total

    def _process(self, session: Session, row: Outbox, now: datetime) -> None:
        try:
            data = self._execute(session, row, now)
        except Exception as exc:
            row.attempts += 1
            error = f"{type(exc).__name__}: {exc}"
            if row.attempts >= self.max_attempts:
                row.status = "failed"
                self._resume(row, {"ok": False, "error": error})
            else:
                row.status = "retry"
                row.next_attempt_at = now + timedelta(
                    seconds=self.base_delay_s * 2 ** (row.attempts - 1)
                )
            audit.append(
                session,
                actor_type="system",
                case_id=row.case_id,
                action="outbox.attempt_failed",
                payload={
                    "action": row.action,
                    "attempt": row.attempts,
                    "error": error,
                    "final": row.status == "failed",
                },
            )
            if not isinstance(exc, AdapterError):
                logger.exception("outbox action %s failed", row.action)
            return
        self._resume(row, {"ok": True, "data": data})
        row.status = "done"
        audit.append(
            session,
            actor_type="system",
            case_id=row.case_id,
            action="outbox.done",
            payload={"action": row.action, "key": row.idempotency_key},
        )

    def _resume(self, row: Outbox, result: dict[str, Any]) -> None:
        if row.action not in RESUMING_ACTIONS:
            return
        try:
            self.runner.dispatch(
                row.case_id, Event("action_result", {"action": row.action, **result})
            )
        except (StaleEvent, CaseClosed):
            logger.info("result for %s already applied", row.idempotency_key)

    # --- Actions --------------------------------------------------------------------------

    def _execute(self, session: Session, row: Outbox, now: datetime) -> dict[str, Any]:
        p, key, case_id = row.payload, row.idempotency_key, row.case_id
        a = self.adapters
        if row.action == "schedule_pickup":
            booking = a.carrier.schedule_pickup(str(case_id), str(p["pincode"]), key)
            if session.scalars(select(Shipment).where(Shipment.awb == booking.awb)).first() is None:
                session.add(
                    Shipment(
                        case_id=case_id,
                        carrier="mock",
                        awb=booking.awb,
                        pickup_slot=booking.slot,
                        status="scheduled",
                        events=["pickup_scheduled"],
                    )
                )
            return {"awb": booking.awb, "slot": booking.slot}
        if row.action == "create_exchange":
            reservation = a.inventory.reserve(str(p["to_sku"]), int(p["qty"]), f"{key}:reserve")
            new_order = a.orders.create_replacement(
                str(p["order_id"]), str(p["item_id"]), f"{key}:order"
            )
            if session.scalars(select(Exchange).where(Exchange.case_id == case_id)).first() is None:
                session.add(
                    Exchange(
                        case_id=case_id,
                        from_sku=p["from_sku"],
                        to_sku=p["to_sku"],
                        reservation_id=reservation,
                        status="created",
                    )
                )
            return {
                "reservation_id": reservation,
                "new_order_ref": new_order,
                "to_sku": p["to_sku"],
            }
        if row.action == "create_replacement":
            new_order = a.orders.create_replacement(str(p["order_id"]), str(p["item_id"]), key)
            existing = session.scalars(
                select(ReplacementOrder).where(ReplacementOrder.case_id == case_id)
            ).first()
            if existing is None:
                session.add(
                    ReplacementOrder(case_id=case_id, new_order_ref=new_order, status="created")
                )
            return {"new_order_ref": new_order}
        if row.action == "refund":
            amount = int(p["amount_minor"])
            result = a.payment.refund(str(p["order_id"]), amount, str(p["method"]), key)
            if session.scalars(select(Refund).where(Refund.idempotency_key == key)).first() is None:
                session.add(
                    Refund(
                        case_id=case_id,
                        amount_minor=amount,
                        method=p["method"],
                        idempotency_key=key,
                        status=result.status,
                        gateway_ref=result.gateway_ref,
                    )
                )
                if result.status != "succeeded":
                    schedule(session, case_id, "refund_tat", now + REFUND_TAT)
            return {
                "gateway_ref": result.gateway_ref,
                "amount_minor": amount,
                "method": p["method"],
                "status": result.status,
            }
        if row.action == "cancel_exchange":
            if p.get("new_order_ref"):
                a.orders.cancel_order(str(p["new_order_ref"]), f"{key}:order")
            if p.get("reservation_id"):
                a.inventory.release(str(p["reservation_id"]), f"{key}:release")
            for exchange in session.scalars(select(Exchange).where(Exchange.case_id == case_id)):
                exchange.status = "cancelled"
            return {}
        if row.action == "cancel_replacement":
            if p.get("new_order_ref"):
                a.orders.cancel_order(str(p["new_order_ref"]), key)
            for repl in session.scalars(
                select(ReplacementOrder).where(ReplacementOrder.case_id == case_id)
            ):
                repl.status = "cancelled"
            return {}
        if row.action == "send_notification":
            return self._send(session, uuid.UUID(p["notification_id"]), now)
        raise ValueError(f"unknown outbox action {row.action!r}")

    def _send(self, session: Session, notification_id: uuid.UUID, now: datetime) -> dict[str, Any]:
        n = session.get(Notification, notification_id)
        if n is None or n.status == "sent":
            return {}
        if n.audience == "staff":
            to = "support-queue"
        else:
            case = session.get(ReturnCase, n.case_id)
            customer = session.get(Customer, case.customer_id) if case else None
            if customer is None:
                raise ValueError("notification has no customer")
            # Contact details are looked up at send time only (never stored in the outbox).
            to = decrypt(customer.phone_enc) or decrypt(customer.email_enc)
        data = {k: str(v) for k, v in n.data.items() if v is not None}
        message_id = self.adapters.notification.send(n.channel, to, n.template, data)
        n.status, n.sent_at = "sent", now
        return {"message_id": message_id}
