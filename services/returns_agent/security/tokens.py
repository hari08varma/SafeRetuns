"""Password hashing (argon2) and JWT access/refresh tokens."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from returns_agent.config import get_settings

_hasher = PasswordHasher()
ALGORITHM = "HS256"


class Role(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    APPROVER = "approver"
    ADMIN = "admin"
    QC_OPERATOR = "qc_operator"
    ANALYST = "analyst"


STAFF_ROLES = frozenset(Role) - {Role.CUSTOMER}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


@dataclass(frozen=True)
class Principal:
    subject: str  # customer id or staff user id
    role: Role


class InvalidToken(Exception):
    pass


def _secret() -> str:
    secret = get_settings().jwt_secret
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be set (at least 32 characters)")
    return secret


def issue_token(principal: Principal, kind: Literal["access", "refresh"]) -> str:
    settings = get_settings()
    ttl = (
        timedelta(minutes=settings.access_token_ttl_min)
        if kind == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    now = datetime.now(UTC)
    claims = {
        "sub": principal.subject,
        "role": principal.role.value,
        "typ": kind,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def decode_token(token: str, kind: Literal["access", "refresh"]) -> Principal:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
    if claims.get("typ") != kind:
        raise InvalidToken(f"expected a {kind} token")
    try:
        return Principal(subject=str(claims["sub"]), role=Role(claims["role"]))
    except (KeyError, ValueError) as exc:
        raise InvalidToken("malformed claims") from exc
