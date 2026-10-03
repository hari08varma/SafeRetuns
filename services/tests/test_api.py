import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.adapters.mock import MockNotificationAdapter
from returns_agent.api.deps import Adapters
from returns_agent.audit import log as audit
from returns_agent.db.models import AuditEvent
from returns_agent.seed.generator import SeedData
from tests.conftest import STAFF_PASSWORD, needs_db

pytestmark = [needs_db]


def _last_code(adapters: Adapters) -> str:
    notifier = adapters.notification
    assert isinstance(notifier, MockNotificationAdapter) and notifier.sent
    data = notifier.sent[-1]["data"]
    assert isinstance(data, dict)
    return str(data["code"])


def customer_login(client: TestClient, adapters: Adapters, phone: str) -> dict[str, str]:
    assert client.post("/api/v1/auth/otp/request", json={"phone": phone}).status_code == 202
    resp = client.post(
        "/api/v1/auth/otp/verify", json={"phone": phone, "code": _last_code(adapters)}
    )
    assert resp.status_code == 200, resp.text
    tokens: dict[str, str] = resp.json()
    return tokens


def staff_login(client: TestClient, role: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/staff/login",
        json={"email": f"{role}@saferetuns.dev", "password": STAFF_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    tokens: dict[str, str] = resp.json()
    return tokens


def auth(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_health_and_request_id(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"x-request-id": "abc123"})
    assert resp.json() == {"status": "ok"} and resp.headers["x-request-id"] == "abc123"


def test_customer_otp_login_and_own_orders_only(
    client: TestClient, adapters: Adapters, seed: SeedData
) -> None:
    alice = seed.customers[0]
    tokens = customer_login(client, adapters, alice.phone)
    orders = client.get("/api/v1/me/orders", headers=auth(tokens)).json()
    expected = {o.id for o in seed.orders if o.customer_id == alice.id}
    assert {o["order_id"] for o in orders} == expected and expected


def test_wrong_code_then_lockout(client: TestClient, adapters: Adapters, seed: SeedData) -> None:
    phone = seed.customers[0].phone
    client.post("/api/v1/auth/otp/request", json={"phone": phone})
    code = _last_code(adapters)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        r = client.post("/api/v1/auth/otp/verify", json={"phone": phone, "code": wrong})
        assert r.status_code == 401
    # Attempts are exhausted: even the right code no longer works.
    r = client.post("/api/v1/auth/otp/verify", json={"phone": phone, "code": code})
    assert r.status_code == 401


def test_code_cannot_be_reused(client: TestClient, adapters: Adapters, seed: SeedData) -> None:
    phone = seed.customers[0].phone
    customer_login(client, adapters, phone)
    reuse = client.post(
        "/api/v1/auth/otp/verify", json={"phone": phone, "code": _last_code(adapters)}
    )
    assert reuse.status_code == 401


def test_otp_rate_limit(client: TestClient, seed: SeedData) -> None:
    phone = seed.customers[0].phone
    codes = [
        client.post("/api/v1/auth/otp/request", json={"phone": phone}).status_code for _ in range(4)
    ]
    assert codes == [202, 202, 202, 429]


def test_unknown_phone_gets_same_response_and_no_message(
    client: TestClient, adapters: Adapters
) -> None:
    r = client.post("/api/v1/auth/otp/request", json={"phone": "+91 9000000000"})
    assert r.status_code == 202
    assert isinstance(adapters.notification, MockNotificationAdapter)
    assert adapters.notification.sent == []


def test_staff_login_and_bad_password(client: TestClient) -> None:
    assert "access_token" in staff_login(client, "agent")
    r = client.post(
        "/api/v1/auth/staff/login",
        json={"email": "agent@saferetuns.dev", "password": "wrong-password"},
    )
    assert r.status_code == 401


def test_refresh_flow(client: TestClient) -> None:
    tokens = staff_login(client, "admin")
    new = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    assert client.get("/api/v1/admin/users", headers=auth(new.json())).status_code == 200
    # An access token cannot be used as a refresh token.
    bad = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert bad.status_code == 401


def test_admin_creates_staff_with_audit(client: TestClient, seeded_db: Session) -> None:
    headers = auth(staff_login(client, "admin"))
    body = {
        "email": "new.agent@saferetuns.dev",
        "password": "a-long-password-1",
        "role": "agent",
        "authority_limit_minor": 100000,
    }
    created = client.post("/api/v1/admin/users", json=body, headers=headers)
    assert created.status_code == 201 and created.json()["role"] == "agent"
    assert client.post("/api/v1/admin/users", json=body, headers=headers).status_code == 409
    actions = seeded_db.scalars(select(AuditEvent.action).order_by(AuditEvent.seq)).all()
    assert "staff.create" in actions
    assert audit.verify(seeded_db).ok


def test_admin_cannot_create_customer_role(client: TestClient) -> None:
    headers = auth(staff_login(client, "admin"))
    body = {"email": "x@saferetuns.dev", "password": "a-long-password-1", "role": "customer"}
    assert client.post("/api/v1/admin/users", json=body, headers=headers).status_code == 422


def test_pii_is_encrypted_at_rest(seeded_db: Session, seed: SeedData) -> None:
    from sqlalchemy import text

    raw = seeded_db.execute(text("SELECT name_enc, phone_enc, email_enc FROM customer")).all()
    plain = {c.name for c in seed.customers} | {c.phone for c in seed.customers}
    assert raw and not any(value in plain for row in raw for value in row)


def test_log_line_carries_request_id(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    from returns_agent.api.app import JsonFormatter

    with caplog.at_level("INFO", logger="returns_agent.http"):
        client.get("/healthz", headers={"x-request-id": "rid-42"})
    line = JsonFormatter().format(caplog.records[-1])
    assert '"request_id": "rid-42"' in line
