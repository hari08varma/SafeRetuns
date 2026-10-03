"""Firebase Phone Auth: only a genuine, phone-verified token for our project signs anyone in."""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from returns_agent.config import get_settings
from returns_agent.security import firebase
from tests.conftest import needs_db

PROJECT = "saferetuns-test"
GOOD_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Keys:
    """Stands in for Google's published signing keys."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("K", (), {"key": GOOD_KEY.public_key()})()


def token(key: Any = GOOD_KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "firebase-uid-1",
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
        "phone_number": "+919812345678",
        "firebase": {"sign_in_provider": "phone"},
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.mark.parametrize(
    "bad",
    [
        token(OTHER_KEY),  # forged signature
        token(aud="someone-else"),  # another Firebase project
        token(iss="https://evil.example"),
        token(exp=int(time.time()) - 3600),  # expired
        token(firebase={"sign_in_provider": "password"}),  # not a phone sign-in
        token(phone_number=None),
    ],
)
def test_untrusted_tokens_are_rejected(bad: str) -> None:
    with pytest.raises(firebase.InvalidFirebaseToken):
        firebase.verify_id_token(bad, PROJECT, Keys())


def test_valid_token_gives_the_verified_phone() -> None:
    identity = firebase.verify_id_token(token(), PROJECT, Keys())
    assert identity.phone == "+919812345678" and identity.uid == "firebase-uid-1"


@needs_db
def test_sign_up_then_profile_then_sign_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "firebase_project_id", PROJECT)
    monkeypatch.setattr(firebase, "_google_keys", lambda: Keys())
    assert (
        client.post("/api/v1/auth/firebase", json={"id_token": token(OTHER_KEY)}).status_code == 401
    )

    first = client.post("/api/v1/auth/firebase", json={"id_token": token()})
    assert first.status_code == 200, first.text
    assert first.json()["profile_complete"] is False  # new account: needs name and address
    auth = {"Authorization": f"Bearer {first.json()['access_token']}"}
    profile = {
        "name": "Anjali Rao",
        "email": "anjali@example.com",
        "address": "Plot 7, Kondapur",
        "city": "Hyderabad",
        "pincode": "500084",
    }
    saved = client.put("/api/v1/me/profile", json=profile, headers=auth)
    assert saved.status_code == 200 and saved.json()["profile_complete"] is True
    assert client.get("/api/v1/me/orders", headers=auth).json() == []

    again = client.post("/api/v1/auth/firebase", json={"id_token": token()})
    assert again.json()["profile_complete"] is True  # same phone: same account, no duplicate
