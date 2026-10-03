from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.config import get_settings


def sqlalchemy_url(url: str) -> str:
    """Use the psycopg 3 driver for plain postgresql:// URLs."""
    return (
        url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://")
        else url
    )


@lru_cache
def get_engine() -> Engine:
    return create_engine(sqlalchemy_url(get_settings().database_url), pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed by the handler."""
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()
