"""Runs the LangGraph spike checks against a real Postgres. Skipped without DATABASE_URL."""

import os

import pytest

pytestmark = pytest.mark.postgres


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_langgraph_spike_checks_pass() -> None:
    from spikes.langgraph_spike import run_all

    results = run_all()
    failed = [(name, detail) for name, ok, detail in results if not ok]
    assert not failed, failed
