import jwt
import pytest

from returns_agent.config import get_settings
from returns_agent.security import pii
from returns_agent.security.tokens import (
    InvalidToken,
    Principal,
    Role,
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)

ALICE = Principal("c-1", Role.CUSTOMER)


def test_password_hashing() -> None:
    h = hash_password("s3cret-password")
    assert h != "s3cret-password" and h.startswith("$argon2")
    assert verify_password(h, "s3cret-password")
    assert not verify_password(h, "wrong")


def test_access_token_round_trip() -> None:
    assert decode_token(issue_token(ALICE, "access"), "access") == ALICE


def test_token_type_is_enforced() -> None:
    with pytest.raises(InvalidToken):
        decode_token(issue_token(ALICE, "access"), "refresh")
    with pytest.raises(InvalidToken):
        decode_token(issue_token(ALICE, "refresh"), "access")


def test_tampered_and_foreign_tokens_rejected() -> None:
    token = issue_token(ALICE, "access")
    forged = jwt.encode(
        {"sub": "c-1", "role": "admin", "typ": "access"}, "other-key" * 5, algorithm="HS256"
    )
    for bad in (token[:-2] + "xx", forged, "garbage"):
        with pytest.raises(InvalidToken):
            decode_token(bad, "access")


def test_expired_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "access_token_ttl_min", -1)
    with pytest.raises(InvalidToken):
        decode_token(issue_token(ALICE, "access"), "access")


def test_pii_encryption_round_trip() -> None:
    a, b = pii.encrypt("+91 9876543210"), pii.encrypt("+91 9876543210")
    assert a != b  # randomised ciphertext
    assert "9876543210" not in a
    assert pii.decrypt(a) == "+91 9876543210"


def test_blind_index_is_deterministic_and_normalised() -> None:
    assert pii.blind_index("+91 98765 43210") == pii.blind_index("+919876543210")
    assert pii.blind_index("A@B.com") == pii.blind_index("a@b.com")
    assert pii.blind_index("+919876543210") != pii.blind_index("+919876543211")


def test_masking() -> None:
    assert pii.mask_phone("+91 98765 43210") == "******3210"
    assert pii.mask_email("priya.sharma@example.com") == "p***@example.com"
