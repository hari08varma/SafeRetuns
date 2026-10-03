"""Field-level encryption for PII, blind indexes for lookups, and display masking."""

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet

from returns_agent.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().pii_encryption_key
    if not key:
        raise RuntimeError("PII_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def blind_index(value: str) -> str:
    """Deterministic keyed hash so encrypted values can be looked up (e.g. phone at login)."""
    key = get_settings().pii_index_key
    if not key:
        raise RuntimeError("PII_INDEX_KEY is not set")
    normalised = "".join(value.split()).lower()
    return hmac.new(key.encode(), normalised.encode(), hashlib.sha256).hexdigest()


def mask_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    return f"******{digits[-4:]}" if len(digits) >= 4 else "****"


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
