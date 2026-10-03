"""The decision record: one receipt per decision, used for explanations, audit and
case summaries. The rationale is written deterministically from the record itself."""

from typing import Any

from returns_agent.decision.config import DecisionConfig
from returns_agent.decision.gate import GateResult
from returns_agent.decision.risk import RiskResult
from returns_agent.decision.scoring import ScoredOption


def rationale(
    scored: list[ScoredOption], risk: RiskResult, conf: float, gate: GateResult, value_minor: int
) -> str:
    best = scored[0]
    others = ", ".join(f"{s.option} ({s.utility:.2f})" for s in scored[1:])
    text = f"Recommended {best.option} (score {best.utility:.2f})"
    if others:
        text += f" over {others}"
    signals = ", ".join(risk.signals) or "no risk signals"
    return (
        f"{text}. Risk {risk.score:.2f} ({signals}); confidence {conf:.2f}; "
        f"value ₹{value_minor / 100:,.2f}. Route {gate.route}: {gate.reason}."
    )


def build_record(
    facts: dict[str, Any],
    scored: list[ScoredOption],
    risk: RiskResult,
    conf: dict[str, float],
    gate: GateResult,
    value_minor: int,
    config: DecisionConfig,
    graph_version: str | None,
) -> dict[str, Any]:
    policy = facts.get("policy") or {}
    request = facts.get("request") or {}
    return {
        "versions": {
            "decision_config": config.version,
            "graph": graph_version,
            "policy": policy.get("policy_version"),
            "legal": policy.get("legal_version"),
            "prompts": {
                node: t.get("prompt_refs") for node, t in (facts.get("llm_trace") or {}).items()
            },
        },
        "facts": {
            "reason": request.get("reason_category"),
            "desired": request.get("desired_resolution"),
            "category": (facts.get("item") or {}).get("category"),
            "value_minor": value_minor,
        },
        "policy": {
            "eligible": policy.get("eligible"),
            "legal_protection": policy.get("legal_protection", []),
            "applied_clauses": [
                t["clause_id"]
                for t in policy.get("trace", [])
                if t.get("result") in ("passed", "applied", "overridden")
            ],
        },
        "options": [s.model_dump() for s in scored],
        "pruned_options": facts.get("pruned_options") or {},
        "chosen": scored[0].option,
        "confidence": conf,
        "risk": risk.model_dump(),
        "route": gate.route,
        "route_reason": gate.reason,
        "kill_switch_applied": gate.kill_switch_applied,
        "rationale": rationale(scored, risk, conf["overall"], gate, value_minor),
    }
