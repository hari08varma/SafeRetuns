"""Loads versioned graphs and compiles each once. A case always runs on the version
it started with, even after a newer version becomes active."""

from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from returns_agent.graph.compiler import CaseState, Chooser, Handler, compile_graph
from returns_agent.graph.lookahead import Feasibility
from returns_agent.graph.schema import GraphSpec, load_graph

Compiled = CompiledStateGraph[CaseState, None, CaseState, CaseState]


class GraphRegistry:
    def __init__(
        self,
        directory: Path,
        active_version: str,
        handlers_for: Any,  # Callable[[GraphSpec, Feasibility], dict[str, Handler]]
        feasibility: Feasibility,
        checkpointer: BaseCheckpointSaver[Any],
        chooser: Chooser | None = None,
        pattern: str = "returns_*.json",
    ) -> None:
        self._specs: dict[str, GraphSpec] = {}
        for path in sorted(directory.glob(pattern)):
            spec = load_graph(path)
            self._specs[spec.version] = spec
        if active_version not in self._specs:
            raise ValueError(f"active graph version '{active_version}' not found in {directory}")
        self.active_version = active_version
        self._handlers_for = handlers_for
        self._feasibility = feasibility
        self._checkpointer = checkpointer
        self._chooser = chooser
        self._compiled: dict[str, Compiled] = {}

    @property
    def versions(self) -> list[str]:
        return sorted(self._specs)

    def spec(self, version: str) -> GraphSpec:
        return self._specs[version]

    def graph(self, version: str) -> Compiled:
        if version not in self._compiled:
            spec = self._specs[version]
            handlers: dict[str, Handler] = self._handlers_for(spec, self._feasibility)
            self._compiled[version] = compile_graph(
                spec, handlers, self._checkpointer, self._feasibility, self._chooser
            )
        return self._compiled[version]
