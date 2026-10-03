"""Shared FastAPI dependencies: adapters, authentication and role checks."""

__all__ = ["Adapters", "current_principal", "get_adapters", "get_cases", "guard_roles", "require"]

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from returns_agent.adapters.bundle import Adapters
from returns_agent.security.tokens import InvalidToken, Principal, Role, decode_token


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
