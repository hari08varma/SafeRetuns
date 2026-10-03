"""Builds the runtime shared by the API server and the worker."""

from dataclasses import dataclass
from functools import partial

from langgraph.checkpoint.postgres import PostgresSaver

from returns_agent.adapters.bundle import Adapters
from returns_agent.agent.cases import CaseService
from returns_agent.config import Settings, config_dir
from returns_agent.db.session import get_engine
from returns_agent.execution.relay import Relay
from returns_agent.graph.nodes import build_handlers
from returns_agent.graph.registry import GraphRegistry
from returns_agent.graph.runner import CaseRunner, make_feasibility
from returns_agent.llm.factory import build_llm_client


@dataclass
class Runtime:
    runner: CaseRunner
    cases: CaseService
    relay: Relay


def build_runtime(settings: Settings, saver: PostgresSaver, adapters: Adapters) -> Runtime:
    llm = build_llm_client(settings)
    registry = GraphRegistry(
        config_dir() / "graphs",
        settings.graph_active_version,
        partial(build_handlers, llm=llm),
        make_feasibility(adapters.inventory, adapters.carrier),
        saver,
    )
    runner = CaseRunner(registry, get_engine())
    return Runtime(
        runner=runner, cases=CaseService(runner, llm), relay=Relay(get_engine(), runner, adapters)
    )
