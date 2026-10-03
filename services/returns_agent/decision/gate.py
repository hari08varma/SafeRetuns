"""Autonomy gate: risk x value x confidence decides auto / approval / escalate."""

from typing import Any, Literal

from pydantic import BaseModel

from returns_agent.decision.config import DecisionConfig

Route = Literal["auto", "approval", "escalate"]


class GateResult(BaseModel):
    route: Route
    reason: str
    kill_switch_applied: bool = False


def confidence(facts: dict[str, Any]) -> dict[str, float]:
    """min(extraction agreement, evidence confidence, identification confidence).
    Values decided by rules (structured input) count as 1.0."""
    understand = (facts.get("llm_trace") or {}).get("UNDERSTAND_REQUEST") or {}
    parts = {
        "extraction": float(understand.get("agreement", 1.0)),
        "evidence": float((facts.get("evidence") or {}).get("confidence", 1.0)),
        "identification": float(facts.get("identification_confidence", 1.0)),
    }
    return {**parts, "overall": min(parts.values())}


def decide_route(
    risk: float, value_minor: int, conf: float, flags: dict[str, Any], config: DecisionConfig
) -> GateResult:
    g = config.gate
    if flags.get("legal_threat") or flags.get("wants_human"):
        return GateResult(route="escalate", reason="customer asked for a person or raised legal")
    if risk >= g.escalate_min_risk:
        return GateResult(route="escalate", reason=f"risk {risk:.2f} >= {g.escalate_min_risk}")
    if conf < g.escalate_below_confidence:
        return GateResult(
            route="escalate", reason=f"confidence {conf:.2f} < {g.escalate_below_confidence}"
        )
    if (
        risk < g.auto_max_risk
        and value_minor < g.auto_max_value_minor
        and (conf >= g.auto_min_confidence)
    ):
        if config.kill_switch:
            return GateResult(
                route="approval",
                reason="kill switch: no automatic decisions",
                kill_switch_applied=True,
            )
        return GateResult(route="auto", reason="low risk, low value, high confidence")
    reasons = []
    if risk >= g.auto_max_risk:
        reasons.append(f"risk {risk:.2f}")
    if value_minor >= g.auto_max_value_minor:
        reasons.append(f"value ₹{value_minor / 100:,.0f}")
    if conf < g.auto_min_confidence:
        reasons.append(f"confidence {conf:.2f}")
    return GateResult(route="approval", reason="needs approval: " + ", ".join(reasons))
