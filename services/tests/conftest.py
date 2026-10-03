import os
import subprocess
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet

# Test-only secrets, set before any settings are read.
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 32)
os.environ.setdefault("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("PII_INDEX_KEY", "test-index-key")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from returns_agent.adapters.mock import (  # noqa: E402
    MockCarrierAdapter,
    MockInventoryAdapter,
    MockNotificationAdapter,
    MockOrderAdapter,
    MockPaymentAdapter,
)
from returns_agent.api.app import create_app  # noqa: E402
from returns_agent.api.deps import Adapters  # noqa: E402
from returns_agent.db.models import Base  # noqa: E402
from returns_agent.seed.generator import SeedData, generate  # noqa: E402

STAFF_PASSWORD = "correct-horse-battery"
needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["alembic", *args],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=os.environ.copy(),
    )


@pytest.fixture(scope="session")
def migrated() -> None:
    """Prove migrations apply, roll back and re-apply cleanly, then leave the DB at head."""
    db_name = os.environ["DATABASE_URL"].rsplit("/", 1)[-1].split("?")[0]
    if not db_name.endswith("_test"):
        raise RuntimeError(f"refusing to reset database '{db_name}': name must end with _test")
    for step in (
        ("downgrade", "base"),
        ("upgrade", "head"),
        ("downgrade", "base"),
        ("upgrade", "head"),
    ):
        result = alembic(*step)
        assert result.returncode == 0, result.stderr


@pytest.fixture
def db(migrated: None) -> Iterator[Session]:
    from returns_agent.db.session import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with Session(engine) as session:
        yield session


@pytest.fixture
def seed() -> SeedData:
    return generate(seed=1, customers=5)


@pytest.fixture
def adapters(seed: SeedData) -> Adapters:
    return Adapters(
        orders=MockOrderAdapter(seed),
        carrier=MockCarrierAdapter(),
        payment=MockPaymentAdapter(),
        inventory=MockInventoryAdapter(),
        notification=MockNotificationAdapter(),
    )


@pytest.fixture
def seeded_db(db: Session, seed: SeedData) -> Session:
    from returns_agent.seed.load import load

    assert load(db, seed, STAFF_PASSWORD)
    return db


@pytest.fixture
def client(seeded_db: Session, adapters: Adapters) -> TestClient:
    return TestClient(create_app(adapters))
