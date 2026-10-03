"""Runs cases on the graph: one event at a time per case, validated against what the
case is waiting for, with the current node mirrored to `return_case` and every
transition written to the audit log."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.adapters.base import CarrierAdapter, InventoryAdapter
from returns_agent.audit import log as audit
from returns_agent.db.models import DecisionRecord, ReturnCase, RiskAssessment
from returns_agent.graph.events import validate_event
from returns_agent.graph.lookahead import Feasibility
from returns_agent.graph.registry import GraphRegistry

RECURSION_LIMIT = 100


class CaseClosed(Exception):
    pass


class StaleEvent(Exception):
    """The event does not match what the case is waiting for (e.g. a duplicate approval)."""


@dataclass(frozen=True)
class Event:
    type: str  # must equal the waiting node's `waits_for`
    payload: dict[str, Any] = field(default_factory=dict)
    actor_type: str = "system"
    actor_id: str | None = None


@dataclass(frozen=True)
class RunResult:
    case_id: uuid.UUID
    current_node: str
    waiting_for: str | None
    status: str
    facts: dict[str, Any]
    violations: list[str]


def make_feasibility(inventory: InventoryAdapter, carrier: CarrierAdapter) -> Feasibility:
    def stock_available(state: dict[str, Any]) -> bool:
        sku = ((state.get("facts") or {}).get("request") or {}).get("exchange_sku")
        return True if not sku else inventory.in_stock(str(sku))  # unknown variant: ask later

    def pincode_serviceable(state: dict[str, Any]) -> bool:
        pincode = (state.get("facts") or {}).get("pincode")
        return bool(pincode) and carrier.is_serviceable(str(pincode))

    return {"stock_available": stock_available, "pincode_serviceable": pincode_serviceable}


class CaseRunner:
    def __init__(self, registry: GraphRegistry, engine: Engine) -> None:
        self.registry = registry
        self._engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def start(self, case_id: uuid.UUID, facts: dict[str, Any]) -> RunResult:
        with self._case_lock(case_id), self._sessions() as session:
            case = session.get(ReturnCase, case_id)
            if case is None:
                raise KeyError(f"case {case_id} not found")
            graph = self.registry.graph(case.graph_version)
            graph.invoke(
                {"case_id": str(case_id), "graph_version": case.graph_version, "facts": facts},
                self._config(case_id),
            )
            return self._mirror(
                session, case, previous_path=0, previous_violations=0, actor=("system", None)
            )

    def dispatch(self, case_id: uuid.UUID, event: Event) -> RunResult:
        with self._case_lock(case_id), self._sessions() as session:
            case = session.get(ReturnCase, case_id)
            if case is None:
                raise KeyError(f"case {case_id} not found")
            graph = self.registry.graph(case.graph_version)
            snapshot = graph.get_state(self._config(case_id))
            if not snapshot.next:
                raise CaseClosed(f"case {case_id} is closed")
            waiting = self.registry.spec(case.graph_version).node(snapshot.next[0])
            if waiting.waits_for != event.type:
                raise StaleEvent(
                    f"case is at {waiting.id} waiting for {waiting.waits_for}, got {event.type}"
                )
            payload = validate_event(event.type, event.payload)  # raises InvalidEvent
            values = snapshot.values
            graph.invoke(Command(resume=payload), self._config(case_id))
            return self._mirror(
                session,
                case,
                previous_path=len(values.get("path", [])),
                previous_violations=len(values.get("violations", [])),
                actor=(event.actor_type, event.actor_id),
            )

    def _mirror(
        self,
        session: Session,
        case: ReturnCase,
        previous_path: int,
        previous_violations: int,
        actor: tuple[str, str | None],
    ) -> RunResult:
        snapshot = self.registry.graph(case.graph_version).get_state(self._config(case.id))
        values = snapshot.values
        path: list[str] = values.get("path", [])
        violations: list[str] = values.get("violations", [])
        waiting_node = snapshot.next[0] if snapshot.next else None
        spec = self.registry.spec(case.graph_version)
        waiting_for = spec.node(waiting_node).waits_for if waiting_node else None

        llm_trace: dict[str, Any] = values.get("facts", {}).get("llm_trace") or {}
        for i, node in enumerate(path[previous_path:]):
            # The resumed node acted on the human's event; everything after is the agent.
            human = i == 0 and actor[0] != "system"
            audit.append(
                session,
                actor_type=actor[0] if human else "ai",
                actor_id=actor[1] if human else None,
                case_id=case.id,
                action="graph.node",
                payload={
                    "node": node,
                    "graph_version": case.graph_version,
                    "llm": llm_trace.get(node),  # prompt versions used by this node
                },
            )
        facts = values.get("facts", {})
        new_nodes = path[previous_path:]
        if "RISK_SCORE" in new_nodes and facts.get("risk"):
            session.add(
                RiskAssessment(
                    case_id=case.id, score=facts["risk"]["score"], signals=facts["risk"]["signals"]
                )
            )
        if "AUTONOMY_GATE" in new_nodes and facts.get("decision"):
            case.route = facts["decision"]["route"]
            session.add(
                DecisionRecord(case_id=case.id, node="AUTONOMY_GATE", record=facts["decision"])
            )
        for violation in violations[previous_violations:]:
            audit.append(
                session,
                actor_type="system",
                case_id=case.id,
                action="graph.violation",
                payload={"violation": violation},
            )

        case.current_node = waiting_node or (path[-1] if path else case.current_node)
        if waiting_node is None:
            case.status, case.closed_at = "closed", datetime.now(UTC)
        else:
            case.status = "escalated" if waiting_node == spec.fallback else "waiting"
        session.commit()
        return RunResult(
            case_id=case.id,
            current_node=case.current_node,
            waiting_for=waiting_for,
            status=case.status,
            facts=dict(values.get("facts", {})),
            violations=violations[previous_violations:],
        )

    @staticmethod
    def _config(case_id: uuid.UUID) -> Any:
        return {"configurable": {"thread_id": str(case_id)}, "recursion_limit": RECURSION_LIMIT}

    @contextmanager
    def _case_lock(self, case_id: uuid.UUID) -> Iterator[None]:
        """Serialises all work on one case across processes (Postgres advisory lock)."""
        key = f"case:{case_id}"
        with self._engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": key})
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
                conn.commit()
