"""Option economics and utility scoring. Deterministic: same inputs, same ranking.

U(o) = w_rev*RevenueRetained - w_cost*NetCost + w_cx*CustomerFit - w_risk*Risk*Exposure
Costs are relative to item value V and scaled into [0, 1] as min(cost / V, 2) / 2.
"""

from typing import Any

from pydantic import BaseModel

from returns_agent.decision.config import DecisionConfig
from returns_agent.decision.risk import item_value_minor

OPTION_ORDER = ["exchange", "replacement", "store_credit", "refund", "keep_item_refund"]
REVENUE_RETAINED = {
    "exchange": 1.0,
    "replacement": 1.0,
    "store_credit": 0.6,
    "refund": 0.0,
    "keep_item_refund": 0.0,
}
EXPOSURE = {
    "exchange": 0.5,
    "replacement": 0.5,
    "store_credit": 0.0,
    "refund": 0.0,
    "keep_item_refund": 1.0,
}  # money or goods released before the item is verified
SUBSTITUTES = {
    frozenset({"refund", "keep_item_refund"}): 1.0,  # a refund without the hassle
    frozenset({"exchange", "replacement"}): 0.7,
    frozenset({"refund", "store_credit"}): 0.5,
}
NO_PREFERENCE_FIT = 0.6
MISMATCH_FIT = 0.2


class ScoredOption(BaseModel):
    option: str
    utility: float
    revenue: float
    cost: float
    customer_fit: float
    exposure: float


def _recovery(facts: dict[str, Any], config: DecisionConfig) -> float:
    return config.recovery(str((facts.get("item") or {}).get("category", "default")))


def keep_item_economical(facts: dict[str, Any], config: DecisionConfig, risk: float) -> bool:
    """Returnless refund when shipping the item back costs more than it can be resold for,
    and only for low-risk cases."""
    value = item_value_minor(facts)
    reverse = config.costs_minor.reverse_shipping + config.costs_minor.handling
    return (
        value > 0
        and _recovery(facts, config) * value < reverse
        and (risk < config.gate.auto_max_risk)
    )


def net_cost_minor(option: str, facts: dict[str, Any], config: DecisionConfig) -> float:
    value = item_value_minor(facts)
    c = config.costs_minor
    writedown = (1 - _recovery(facts, config)) * value  # returned goods are worth less
    reverse = c.reverse_shipping + c.handling
    if option == "keep_item_refund":
        return float(value)  # nothing comes back
    if option in ("exchange", "replacement"):
        return reverse + c.forward_shipping + writedown
    return reverse + writedown  # refund, store_credit


def customer_fit(option: str, desired: str | None) -> float:
    if not desired:
        return NO_PREFERENCE_FIT
    if option == desired:
        return 1.0
    return SUBSTITUTES.get(frozenset({option, desired}), MISMATCH_FIT)


def score_options(
    options: list[str], facts: dict[str, Any], config: DecisionConfig, risk: float
) -> list[ScoredOption]:
    """Returns options best first; ties go to the customer's request, then OPTION_ORDER."""
    value = max(1, item_value_minor(facts))
    desired = (facts.get("request") or {}).get("desired_resolution")
    w = config.weights
    scored = []
    for option in options:
        cost = min(net_cost_minor(option, facts, config) / value, 2.0) / 2
        fit = customer_fit(option, desired)
        revenue, exposure = REVENUE_RETAINED.get(option, 0.0), EXPOSURE.get(option, 0.0)
        utility = (
            w.revenue * revenue - w.cost * cost + w.customer_fit * fit - w.risk * risk * exposure
        )
        scored.append(
            ScoredOption(
                option=option,
                utility=round(utility, 6),
                revenue=revenue,
                cost=round(cost, 6),
                customer_fit=fit,
                exposure=exposure,
            )
        )

    def rank(s: ScoredOption) -> tuple[float, int, int]:
        order = OPTION_ORDER.index(s.option) if s.option in OPTION_ORDER else len(OPTION_ORDER)
        return (-s.utility, 0 if s.option == desired else 1, order)

    return sorted(scored, key=rank)
