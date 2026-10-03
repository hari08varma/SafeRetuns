"""Customer login by one-time code sent to the phone on file."""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from returns_agent.adapters.base import NotificationAdapter
from returns_agent.config import get_settings
from returns_agent.db.models import Customer, OtpChallenge
from returns_agent.security.pii import blind_index


class OtpRateLimited(Exception):
    pass


class InvalidOtp(Exception):
    pass


def _code_hash(phone_index: str, code: str) -> str:
    key = get_settings().pii_index_key.encode()
    return hmac.new(key, f"{phone_index}:{code}".encode(), hashlib.sha256).hexdigest()


def request_otp(session: Session, phone: str, notifier: NotificationAdapter) -> str | None:
    """Sends a code if the phone belongs to a customer. Same response either way,
    so the endpoint cannot be used to discover which numbers are registered.
    Returns the code that was sent (for the development echo only), else None."""
    settings = get_settings()
    idx = blind_index(phone)
    now = datetime.now(UTC)
    recent = (
        session.scalar(
            select(func.count())
            .select_from(OtpChallenge)
            .where(
                OtpChallenge.phone_index == idx,
                OtpChallenge.created_at > now - timedelta(seconds=settings.otp_window_s),
            )
        )
        or 0
    )
    if recent >= settings.otp_max_requests_per_window:
        raise OtpRateLimited()
    code = f"{secrets.randbelow(1_000_000):06d}"
    session.add(
        OtpChallenge(
            phone_index=idx,
            code_hash=_code_hash(idx, code),
            expires_at=now + timedelta(seconds=settings.otp_ttl_s),
        )
    )
    if session.scalar(select(Customer.id).where(Customer.phone_index == idx)):
        notifier.send("sms", phone, "otp_login", {"code": code})
        return code
    return None


def verify_otp(session: Session, phone: str, code: str) -> uuid.UUID:
    """Returns the customer id. Failed attempts are counted; the caller must commit."""
    settings = get_settings()
    idx = blind_index(phone)
    now = datetime.now(UTC)
    challenge = session.scalars(
        select(OtpChallenge)
        .where(
            OtpChallenge.phone_index == idx,
            OtpChallenge.consumed_at.is_(None),
            OtpChallenge.expires_at > now,
        )
        .order_by(OtpChallenge.created_at.desc())
        .limit(1)
        .with_for_update()
    ).first()
    if challenge is None:
        raise InvalidOtp("no active code")
    challenge.attempts += 1
    if challenge.attempts > settings.otp_max_attempts:
        raise InvalidOtp("too many attempts")
    if not hmac.compare_digest(challenge.code_hash, _code_hash(idx, code)):
        raise InvalidOtp("wrong code")
    customer_id = session.scalar(select(Customer.id).where(Customer.phone_index == idx))
    if customer_id is None:
        raise InvalidOtp("unknown customer")
    challenge.consumed_at = now
    return customer_id
