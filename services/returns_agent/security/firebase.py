"""Firebase Phone Auth: Firebase sends and checks the SMS code; we verify the resulting ID
token ourselves (signature with Google's published keys, audience, issuer, expiry) and only
then trust the phone number in it. No Firebase admin credentials are needed."""

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import jwt

from returns_agent.config import get_settings

GOOGLE_JWKS = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
CLOCK_SKEW_S = 60


class InvalidFirebaseToken(Exception):
    pass


class KeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True)
class FirebaseIdentity:
    uid: str
    phone: str  # E.164, e.g. +919876543210


@lru_cache
def _google_keys() -> KeyResolver:
    return jwt.PyJWKClient(GOOGLE_JWKS, cache_keys=True, lifespan=3600)


def verify_id_token(
    token: str, project_id: str | None = None, keys: KeyResolver | None = None
) -> FirebaseIdentity:
    project_id = project_id or get_settings().firebase_project_id
    if not project_id:
        raise InvalidFirebaseToken("Firebase sign-in is not configured")
    try:
        key = (keys or _google_keys()).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            leeway=CLOCK_SKEW_S,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidFirebaseToken("invalid sign-in token") from exc
    if not claims.get("sub") or claims.get("auth_time", 0) > time.time() + CLOCK_SKEW_S:
        raise InvalidFirebaseToken("invalid sign-in token")
    if (claims.get("firebase") or {}).get("sign_in_provider") != "phone":
        raise InvalidFirebaseToken("sign in with your phone number")
    phone = claims.get("phone_number")
    if not isinstance(phone, str) or not phone.startswith("+"):
        raise InvalidFirebaseToken("the token has no verified phone number")
    return FirebaseIdentity(uid=str(claims["sub"]), phone=phone)
