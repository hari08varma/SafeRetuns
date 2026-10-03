"""Deterministic policy evaluation. Legal rules are evaluated first and cannot be
overridden by merchant rules; every evaluated clause is recorded in the trace."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from returns_agent.graph.conditions import evaluate, resolve_var
from returns_agent.policy.schema import ALL_REFUND_METHODS, ALL_RESOLUTIONS, PolicyDoc, Rule

ReasonCategory = Literal[
    "size_fit", "damaged", "defective", "wrong_item", "not_as_described", "changed_mind", "other"
]


class ItemFacts(BaseModel):
    sku: str
    category: str
    final_sale: bool = False
    qty_ordered: int = Field(ge=1)
    qty_returning: int
    qty_already_returned: int = 0


class OrderFacts(BaseModel):
    order_id: str
    status: str
    payment_method: str
    placed_at: datetime
    delivered_at: datetime | None = None


class RequestFacts(BaseModel):
    reason_category: ReasonCategory
    is_gift: bool = False


class PolicyFacts(BaseModel):
    order: OrderFacts
    item: ItemFacts
    request: RequestFacts


TraceResult = Literal["not_applicable", "passed", "failed", "overridden", "applied"]


class TraceEntry(BaseModel):
    clause_id: str
    layer: Literal["legal", "merchant"]
    result: TraceResult
    inputs: dict[str, Any]


class PolicyDecision(BaseModel):
    eligible: bool
    reason_codes: list[str]
    allowed_resolutions: list[str]
    refund_methods: list[str]
    required_evidence: list[str]
    restocking_fee_pct: int
    shipping_refundable: bool
    window_remaining_days: int | None
    legal_protection: list[str]  # legal clauses that applied
    legal_version: str
    policy_version: str
    trace: list[TraceEntry]


def _referenced_vars(logic: Any) -> list[str]:
    found: list[str] = []
    if isinstance(logic, dict):
        for op, args in logic.items():
            if op == "var":
                path = args[0] if isinstance(args, list) else args
                found.append(str(path))
            else:
                found.extend(_referenced_vars(args))
    elif isinstance(logic, list):
        for a in logic:
            found.extend(_referenced_vars(a))
    return found


def _inputs(rule: Rule, data: dict[str, Any]) -> dict[str, Any]:
    paths = _referenced_vars(rule.applies_to) + _referenced_vars(rule.require)
    return {p: resolve_var(p, data) for p in dict.fromkeys(paths)}


def _data(facts: PolicyFacts, merchant: PolicyDoc, as_of: datetime) -> dict[str, Any]:
    delivered = facts.order.delivered_at
    days = (as_of - delivered).days if delivered else None
    return {
        **facts.model_dump(mode="json"),
        "days_since_delivery": days,
        "return_window_days": merchant.return_window_days,
    }


def evaluate_policy(
    legal: PolicyDoc, merchant: PolicyDoc, facts: PolicyFacts, as_of: datetime
) -> PolicyDecision:
    data = _data(facts, merchant, as_of)
    trace: list[TraceEntry] = []
    protection: list[str] = []
    guaranteed: set[str] = set()
    waive_fees = False

    for rule in legal.rules:
        applies = bool(evaluate(rule.applies_to, data))
        trace.append(
            TraceEntry(
                clause_id=rule.clause_id,
                layer="legal",
                result="applied" if applies else "not_applicable",
                inputs=_inputs(rule, data),
            )
        )
        if applies:
            protection.append(rule.clause_id)
            guaranteed |= set(rule.effects.guarantee_resolutions)
            waive_fees = waive_fees or rule.effects.waive_fees

    eligible = True
    reasons: list[str] = []
    resolutions = set(ALL_RESOLUTIONS)
    methods = set(ALL_REFUND_METHODS)
    evidence: set[str] = set()
    fee_pct = 0
    shipping_refundable = True

    for rule in merchant.rules:
        inputs = _inputs(rule, data)
        if not evaluate(rule.applies_to, data):
            trace.append(
                TraceEntry(
                    clause_id=rule.clause_id,
                    layer="merchant",
                    result="not_applicable",
                    inputs=inputs,
                )
            )
            continue
        if rule.require is not None and not evaluate(rule.require, data):
            if protection and rule.legal_overridable:
                trace.append(
                    TraceEntry(
                        clause_id=rule.clause_id,
                        layer="merchant",
                        result="overridden",
                        inputs=inputs,
                    )
                )
                continue
            eligible = False
            reasons.append(rule.reason_code or rule.clause_id)
            trace.append(
                TraceEntry(
                    clause_id=rule.clause_id, layer="merchant", result="failed", inputs=inputs
                )
            )
            continue
        fx = rule.effects
        if fx.allow_resolutions is not None:
            resolutions &= set(fx.allow_resolutions)
        if fx.refund_methods is not None:
            methods &= set(fx.refund_methods)
        evidence |= set(fx.require_evidence)
        fee_pct = max(fee_pct, fx.restocking_fee_pct)
        if fx.shipping_refundable is False:
            shipping_refundable = False
        trace.append(
            TraceEntry(
                clause_id=rule.clause_id,
                layer="merchant",
                result="passed" if rule.require is not None else "applied",
                inputs=inputs,
            )
        )

    if protection:
        resolutions |= guaranteed
    if waive_fees:
        fee_pct, shipping_refundable = 0, True

    days = data["days_since_delivery"]
    window = merchant.return_window_days
    remaining = max(0, window - days) if days is not None and window is not None else None
    return PolicyDecision(
        eligible=eligible,
        reason_codes=reasons,
        allowed_resolutions=sorted(resolutions) if eligible else [],
        refund_methods=sorted(methods) if eligible else [],
        required_evidence=sorted(evidence),
        restocking_fee_pct=fee_pct,
        shipping_refundable=shipping_refundable,
        window_remaining_days=remaining,
        legal_protection=protection,
        legal_version=legal.version,
        policy_version=merchant.version,
        trace=trace,
    )
