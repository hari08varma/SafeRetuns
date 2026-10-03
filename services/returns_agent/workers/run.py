"""Background worker: outbox relay, timers and reconciliation, in one loop.

Usage: python -m returns_agent.workers.run
Several workers can run at once: rows are claimed with SELECT ... FOR UPDATE SKIP LOCKED.
"""

import logging
import signal
import time
from types import FrameType

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.adapters.mock_bundle import mock_adapters
from returns_agent.api.app import configure_logging
from returns_agent.config import get_settings
from returns_agent.db.models import ReturnCase
from returns_agent.db.session import get_engine
from returns_agent.execution.actions import ACTION_NODES
from returns_agent.lifecycle import timers
from returns_agent.runtime import Runtime, build_runtime

logger = logging.getLogger("returns_agent.worker")
POLL_INTERVAL_S = 2.0
RECONCILE_EVERY = 30  # loops


def reconcile_all(runtime: Runtime, sessions: sessionmaker[Session]) -> int:
    with sessions() as session:
        waiting = session.scalars(
            select(ReturnCase.id).where(
                ReturnCase.status == "waiting", ReturnCase.current_node.in_(ACTION_NODES)
            )
        ).all()
    return sum(runtime.runner.reconcile(case_id) for case_id in waiting)


def run_once(runtime: Runtime, sessions: sessionmaker[Session]) -> int:
    work = runtime.relay.run_once()
    work += timers.tick(sessions, runtime.runner.timeout)
    return work


def main() -> None:
    configure_logging()
    settings = get_settings()
    stopping = False

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sessions = sessionmaker(bind=get_engine(), expire_on_commit=False)
    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()
        runtime = build_runtime(settings, saver, mock_adapters())
        logger.info("worker started")
        loops = 0
        while not stopping:
            try:
                work = run_once(runtime, sessions)
                if loops % RECONCILE_EVERY == 0:
                    work += reconcile_all(runtime, sessions)
            except Exception:  # keep the worker alive; each unit of work is retried
                logger.exception("worker loop failed")
                work = 0
            loops += 1
            if not work:
                time.sleep(POLL_INTERVAL_S)
        logger.info("worker stopped")


if __name__ == "__main__":
    main()
