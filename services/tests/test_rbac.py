"""Every protected endpoint declares its roles, and the role matrix is enforced."""

import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from returns_agent.api.app import create_app
from returns_agent.api.deps import Adapters, guard_roles
from returns_agent.api.routes import router as api_router
from returns_agent.security.tokens import Principal, Role, issue_token
from returns_agent.seed.generator import SeedData
from tests.conftest import needs_db

PUBLIC = {
    "/healthz",
    "/api/v1/auth/otp/request",
    "/api/v1/auth/otp/verify",
    "/api/v1/auth/staff/login",
    "/api/v1/auth/refresh",
}

EXPECTED: dict[tuple[str, str], set[Role]] = {
    ("GET", "/api/v1/me/orders"): {Role.CUSTOMER},
    ("GET", "/api/v1/console/cases"): {Role.AGENT, Role.APPROVER, Role.ADMIN},
    ("GET", "/api/v1/admin/users"): {Role.ADMIN},
    ("POST", "/api/v1/admin/users"): {Role.ADMIN},
    ("GET", "/api/v1/analytics/summary"): {Role.ANALYST, Role.ADMIN},
    ("POST", "/api/v1/cases"): {Role.CUSTOMER},
    ("POST", "/api/v1/cases/{case_id}/messages"): {Role.CUSTOMER},
    ("POST", "/api/v1/cases/{case_id}/confirm"): {Role.CUSTOMER},
    ("GET", "/api/v1/cases/{case_id}/messages"): {Role.CUSTOMER},
}

BODIES = {
    ("POST", "/api/v1/admin/users"): None,  # filled per role below (unique email)
    ("POST", "/api/v1/cases"): {"order_id": "ORD-X", "item_id": "ORD-X-1", "message": "hi"},
    ("POST", "/api/v1/cases/{case_id}/messages"): {"text": "hi"},
    ("POST", "/api/v1/cases/{case_id}/confirm"): {"accept": True},
}


def _routes(adapters: Adapters) -> list[APIRoute]:
    # App-level routes plus the API router's routes (FastAPI wraps included routers).
    app_routes = [r for r in create_app(adapters).routes if isinstance(r, APIRoute)]
    return app_routes + [r for r in api_router.routes if isinstance(r, APIRoute)]


def test_every_non_public_route_is_guarded_as_expected(adapters: Adapters) -> None:
    seen: dict[tuple[str, str], set[Role]] = {}
    for route in _routes(adapters):
        if route.path in PUBLIC:
            continue
        roles = [guard_roles(d.call) for d in route.dependant.dependencies if d.call]
        guards = [r for r in roles if r is not None]
        assert len(guards) == 1, f"{route.path} must have exactly one role guard"
        for method in route.methods:
            seen[(method, route.path)] = set(guards[0])
    assert seen == EXPECTED, "update EXPECTED when adding endpoints"


@needs_db
@pytest.mark.parametrize(("method", "path"), sorted(EXPECTED))
@pytest.mark.parametrize("role", list(Role))
def test_role_matrix(
    client: TestClient, seed: SeedData, method: str, path: str, role: Role
) -> None:
    from sqlalchemy import select

    from returns_agent.db.models import Customer
    from returns_agent.db.session import get_engine

    with get_engine().connect() as conn:
        customer_id = conn.execute(select(Customer.id).limit(1)).scalar_one()
    token = issue_token(Principal(str(customer_id), role), "access")
    body = BODIES.get((method, path))
    if path == "/api/v1/admin/users" and method == "POST":
        body = {
            "email": f"m-{role.value}@saferetuns.dev",
            "password": "a-long-password-1",
            "role": "agent",
        }
    url = path.replace("{case_id}", str(uuid.uuid4()))
    resp = client.request(method, url, json=body, headers={"Authorization": f"Bearer {token}"})
    if role in EXPECTED[(method, path)]:
        # Authorised: anything but an auth failure (case endpoints may 404/503 here).
        assert resp.status_code not in (401, 403), resp.text
    else:
        assert resp.status_code == 403


@needs_db
def test_missing_or_invalid_token_is_401(client: TestClient) -> None:
    assert client.get("/api/v1/admin/users").status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.get("/api/v1/admin/users", headers=bad).status_code == 401
