"""Server entrypoint: `uvicorn returns_agent.api.main:app`.

External systems use mock adapters until real integrations exist (Phase 2 of the plan).
Side effects run in the worker: `python -m returns_agent.workers.run`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver

from returns_agent.adapters.mock_bundle import mock_adapters
from returns_agent.api.app import configure_logging, create_app
from returns_agent.config import get_settings
from returns_agent.runtime import build_runtime

configure_logging()
adapters = mock_adapters()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()
        app.state.cases = build_runtime(settings, saver, adapters).cases
        yield


app = create_app(adapters, lifespan=lifespan)
