"""Server entrypoint: `uvicorn returns_agent.api.main:app`.

External systems use mock adapters until real integrations exist (Phase 2 of the plan).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver

from returns_agent.adapters.mock import (
    MockCarrierAdapter,
    MockInventoryAdapter,
    MockNotificationAdapter,
    MockOrderAdapter,
    MockPaymentAdapter,
)
from returns_agent.agent.cases import CaseService
from returns_agent.api.app import configure_logging, create_app
from returns_agent.api.deps import Adapters
from returns_agent.config import config_dir, get_settings
from returns_agent.db.session import get_engine
from returns_agent.graph.nodes import build_handlers
from returns_agent.graph.registry import GraphRegistry
from returns_agent.graph.runner import CaseRunner, make_feasibility
from returns_agent.llm.factory import build_llm_client
from returns_agent.seed.generator import generate

configure_logging()
_seed = generate()
adapters = Adapters(
    orders=MockOrderAdapter(_seed),
    carrier=MockCarrierAdapter(),
    payment=MockPaymentAdapter(),
    inventory=MockInventoryAdapter({p.sku: 20 for p in _seed.products}),
    notification=MockNotificationAdapter(),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    llm = build_llm_client(settings)
    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()
        registry = GraphRegistry(
            config_dir() / "graphs",
            settings.graph_active_version,
            partial(build_handlers, llm=llm),
            make_feasibility(adapters.inventory, adapters.carrier),
            saver,
        )
        app.state.cases = CaseService(CaseRunner(registry, get_engine()), llm)
        yield


app = create_app(adapters, lifespan=lifespan)
