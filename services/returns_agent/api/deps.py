"""Shared FastAPI dependencies: adapters, authentication and role checks."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from returns_agent.adapters.base import (
    CarrierAdapter,
    InventoryAdapter,
    NotificationAdapter,
    OrderAdapter,
    PaymentAdapter,
)
from returns_agent.security.tokens import InvalidToken, Principal, Role, decode_token


@dataclass
class Adapters:
    orders: OrderAdapter
    carrier: CarrierAdapter
    payment: PaymentAdapter
    inventory: InventoryAdapter
    notification: NotificationAdapter


def get_adapters(request: Request) -> Adapters:
    adapters: Adapters = request.app.state.adapters
    return adapters


def get_cases(request: Request) -> Any:
    cases = getattr(request.app.state, "cases", None)
    if cases is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "case service not configured")
    return cases


_bearer = HTTPBearer(auto_error=False)


def current_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        return decode_token(creds.credentials, "access")
    except InvalidToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


def require(*roles: Role) -> Any:
    """Route guard. Every protected route must use it; tests enforce this."""
    allowed = frozenset(roles)

    def guard(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "role not allowed")
        return principal

    guard.allowed_roles = allowed  # type: ignore[attr-defined]
    return Depends(guard)


def guard_roles(dependency: Callable[..., Any]) -> frozenset[Role] | None:
    roles: frozenset[Role] | None = getattr(dependency, "allowed_roles", None)
    return roles
