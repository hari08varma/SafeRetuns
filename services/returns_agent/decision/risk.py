"""Rule-based risk score with named signals (Phase 8 adds evidence signals and a model).
Risk only routes cases to humans; it never rejects on its own."""

from typing import Any

from pydantic import BaseModel

from returns_agent.decision.config import DecisionConfig


class RiskResult(BaseModel):
    score: float
    signals: list[str]


def item_value_minor(facts: dict[str, Any]) -> int:
    quote = facts.get("refund_quote") or {}
    if quote.get("total_minor"):
        return int(quote["total_minor"])
    pricing = facts.get("pricing") or {}
    item = facts.get("item") or {}
    unit = int(pricing.get("unit_price_minor", 0))
    qty_ordered = max(1, int(item.get("qty_ordered", 1)))
    returning = int(item.get("qty_returning", 1))
    line = unit * qty_ordered - int(pricing.get("line_discount_minor", 0))
    return line * returning // qty_ordered


def assess_risk(facts: dict[str, Any], config: DecisionConfig) -> RiskResult:
    w = config.risk
    stats = facts.get("customer_stats") or {}
    returns = int(stats.get("returns_90d", 0))
    orders = int(stats.get("orders_90d", 0))
    value = item_value_minor(facts)
    # Days since delivery as the policy engine saw it (keeps this deterministic).
    days_since_delivery = next(
        (
            t["inputs"].get("days_since_delivery")
            for t in (facts.get("policy") or {}).get("trace", [])
            if "days_since_delivery" in t.get("inputs", {})
        ),
        None,
    )

    signals: dict[str, float] = {}
    if returns >= 2 and orders and returns / orders >= 0.5:
        signals["high_return_ratio"] = w.high_return_ratio
    if returns >= 5:
        signals["serial_returner"] = w.serial_returner
    if int(stats.get("account_age_days", 365)) < 30:
        signals["new_account"] = w.new_account
    if value >= w.high_value_minor:
        signals["high_value"] = w.high_value
    if days_since_delivery == 0:
        signals["same_day_return"] = w.same_day_return
    if (facts.get("order") or {}).get(
        "payment_method"
    ) == "cod" and value >= w.cod_high_value_minor:
        signals["cod_high_value"] = w.cod_high_value
    return RiskResult(score=round(min(1.0, sum(signals.values())), 4), signals=sorted(signals))
