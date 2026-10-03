"""Inbound carrier and payment webhooks: HMAC-signed, timestamp-checked (replay window)
and de-duplicated by event id, so a delivery is applied at most once."""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from returns_agent.audit import log as audit
from returns_agent.db.models import Refund, Shipment, WebhookEvent
from returns_agent.graph.runner import CaseClosed, CaseRunner, Event, StaleEvent
from returns_agent.lifecycle.notify import notify
from returns_agent.lifecycle.timers import resolve


class WebhookAuthError(Exception):
    pass


class CarrierWebhook(BaseModel):
    event_id: str
    awb: str
    event: Literal["picked_up", "in_transit", "received", "pickup_failed"]


class PaymentWebhook(BaseModel):
    event_id: str
    gateway_ref: str
    status: Literal["succeeded", "failed"]


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    tolerance_s: int,
    now: float | None = None,
) -> None:
    if not secret:
        raise WebhookAuthError("webhooks are not configured")
    if not timestamp or not signature:
        raise WebhookAuthError("missing signature headers")
    try:
        age = abs((now if now is not None else time.time()) - int(timestamp))
    except ValueError as exc:
        raise WebhookAuthError("bad timestamp") from exc
    if age > tolerance_s:
        raise WebhookAuthError("timestamp outside the allowed window")
    if not hmac.compare_digest(sign(secret, timestamp, body), signature):
        raise WebhookAuthError("bad signature")


def _first_delivery(session: Session, source: str, event_id: str) -> bool:
    result = session.execute(
        insert(WebhookEvent)
        .values(id=f"{source}:{event_id}", source=source)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(WebhookEvent.id)
    )
    return result.first() is not None


def handle_carrier(session: Session, runner: CaseRunner, hook: CarrierWebhook) -> str:
    if not _first_delivery(session, "carrier", hook.event_id):
        return "duplicate"
    shipment = session.scalars(select(Shipment).where(Shipment.awb == hook.awb)).first()
    if shipment is None:
        session.commit()
        return "unknown_shipment"
    shipment.events = [*shipment.events, hook.event]
    if hook.event != "pickup_failed":
        shipment.status = hook.event
    case_id = shipment.case_id
    session.commit()  # record the delivery before resuming the case
    try:
        runner.dispatch(
            case_id,
            Event("carrier_event", {"event": hook.event, "awb": hook.awb}, "system", "carrier"),
        )
    except (StaleEvent, CaseClosed):
        return "ignored"
    return "applied"


def handle_payment(session: Session, hook: PaymentWebhook) -> str:
    if not _first_delivery(session, "payment", hook.event_id):
        return "duplicate"
    refund = session.scalars(select(Refund).where(Refund.gateway_ref == hook.gateway_ref)).first()
    if refund is None:
        session.commit()
        return "unknown_refund"
    refund.status = hook.status
    if hook.status == "succeeded":
        resolve(session, refund.case_id, datetime.now(UTC), kinds=("refund_tat",))
    else:
        audit.append(
            session,
            actor_type="system",
            case_id=refund.case_id,
            action="refund.failed",
            payload={"gateway_ref": hook.gateway_ref},
        )
        notify(
            session,
            refund.case_id,
            "refund_failed",
            audience="staff",
            data={"gateway_ref": hook.gateway_ref},
        )
    session.commit()
    return "applied"
