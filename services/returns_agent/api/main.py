"""Server entrypoint: `uvicorn returns_agent.api.main:app`.

External systems use mock adapters until real integrations exist (Phase 2 of the plan).
Side effects run in the worker: `python -m returns_agent.workers.run`, or inside this
process when EMBEDDED_WORKER=true (single-service hosting).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver

from returns_agent.adapters.mock_bundle import mock_adapters
from returns_agent.api.app import configure_logging, create_app
from returns_agent.config import get_settings
from returns_agent.runtime import build_runtime
from returns_agent.workers.run import start_embedded

configure_logging()
adapters = mock_adapters()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()
        runtime = build_runtime(settings, saver, adapters)
        app.state.cases = runtime.cases
        worker = start_embedded(runtime) if settings.embedded_worker else None
        yield
        if worker is not None:
            thread, stop = worker
            stop.set()
            thread.join(timeout=10)


app = create_app(adapters, lifespan=lifespan)
