"""Decision layer: config limits, risk signals, economics, scoring, gate, records."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from returns_agent.config import config_dir
from returns_agent.decision.config import DecisionConfig, load_decision_config
from returns_agent.decision.gate import confidence, decide_route
from returns_agent.decision.record import build_record
from returns_agent.decision.risk import RiskResult, assess_risk, item_value_minor
from returns_agent.decision.scoring import keep_item_economical, net_cost_minor, score_options

CFG = load_decision_config(config_dir() / "decision.yaml")


def facts(
    price: int = 129900, category: str = "apparel", desired: str | None = "refund", **extra: Any
) -> dict[str, Any]:
    return {
        "item": {"category": category, "qty_ordered": 1, "qty_returning": 1},
        "pricing": {"unit_price_minor": price},
        "request": {"reason_category": "size_fit", "desired_resolution": desired},
        "order": {"payment_method": "upi"},
        **extra,
    }


# --- Config ---------------------------------------------------------------------------------


def test_shipped_config_loads() -> None:
    assert CFG.version and not CFG.kill_switch and CFG.recovery("unknown") == 0.7


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("gate", "auto_max_risk"), 0.55),  # above the hard limit 0.5
        (("gate", "auto_max_value_minor"), 3_000_000),  # above ₹25,000
        (("gate", "auto_min_confidence"), 0.6),  # below 0.7
        (("gate", "escalate_min_risk"), 0.2),  # below auto_max_risk
        (("recovery_rate", "beauty"), 1.5),
    ],
)
def test_config_outside_limits_is_rejected(
    tmp_path: Path, path: tuple[str, str], value: float
) -> None:
    data = yaml.safe_load((config_dir() / "decision.yaml").read_text())
    data[path[0]][path[1]] = value
    with pytest.raises(ValidationError):
        DecisionConfig.model_validate(data)


# --- Risk -------------------------------------------------------------------------------------


def test_clean_customer_has_no_risk() -> None:
    r = assess_risk(
        facts(customer_stats={"returns_90d": 0, "orders_90d": 4, "account_age_days": 400}), CFG
    )
    assert r == RiskResult(score=0.0, signals=[])


def test_risk_signals_add_up_and_are_named() -> None:
    f = facts(
        price=2_500_000,
        customer_stats={"returns_90d": 6, "orders_90d": 8, "account_age_days": 10},
        order={"payment_method": "cod"},
        policy={"trace": [{"clause_id": "RET-WINDOW-01", "inputs": {"days_since_delivery": 0}}]},
    )
    r = assess_risk(f, CFG)
    assert r.signals == [
        "cod_high_value",
        "high_return_ratio",
        "high_value",
        "new_account",
        "same_day_return",
        "serial_returner",
    ]
    assert r.score == 1.0  # capped


# --- Economics and scoring ----------------------------------------------------------------------


def test_keep_item_only_when_return_costs_more_than_recovery() -> None:
    serum = facts(price=79900, category="beauty")  # recovery 0.1 x 799 < ₹110 to ship back
    kurta = facts(price=129900, category="apparel")  # recovery 0.8 x 1299 > ₹110
    assert keep_item_economical(serum, CFG, risk=0.0)
    assert not keep_item_economical(kurta, CFG, risk=0.0)
    assert not keep_item_economical(serum, CFG, risk=0.3)  # never for risky cases


def test_keep_item_is_selected_when_economical() -> None:
    serum = facts(price=79900, category="beauty", desired="refund")
    ranked = score_options(["refund", "store_credit", "keep_item_refund"], serum, CFG, risk=0.0)
    assert ranked[0].option == "keep_item_refund"
    assert net_cost_minor("keep_item_refund", serum, CFG) < net_cost_minor("refund", serum, CFG)


@pytest.mark.parametrize(
    ("desired", "expected"),
    [
        ("refund", "refund"),
        ("exchange", "exchange"),
        ("store_credit", "store_credit"),
        (None, "exchange"),
    ],
)
def test_customer_request_drives_choice_for_ordinary_items(
    desired: str | None, expected: str
) -> None:
    ranked = score_options(["exchange", "store_credit", "refund"], facts(desired=desired), CFG, 0.0)
    assert ranked[0].option == expected


def test_risk_penalises_options_that_release_value_early() -> None:
    f = facts(desired=None)
    calm = {s.option: s.utility for s in score_options(["exchange", "refund"], f, CFG, 0.0)}
    risky = {s.option: s.utility for s in score_options(["exchange", "refund"], f, CFG, 0.5)}
    assert risky["exchange"] < calm["exchange"] and risky["refund"] == calm["refund"]


def test_scoring_is_deterministic() -> None:
    a = score_options(["exchange", "replacement", "refund"], facts(), CFG, 0.2)
    assert a == score_options(["exchange", "replacement", "refund"], facts(), CFG, 0.2)


# --- Confidence and gate ------------------------------------------------------------------------


def test_confidence_is_the_weakest_part() -> None:
    f = {"llm_trace": {"UNDERSTAND_REQUEST": {"agreement": 2 / 3}}, "evidence": {"confidence": 0.9}}
    c = confidence(f)
    assert c["overall"] == pytest.approx(2 / 3) and c["identification"] == 1.0
    assert confidence({})["overall"] == 1.0  # all rule-decided


V = CFG.gate.auto_max_value_minor  # ₹5,000


@pytest.mark.parametrize(
    ("risk", "value", "conf", "flags", "route"),
    [
        (0.0, 129900, 1.0, {}, "auto"),
        (0.29, V - 1, 0.8, {}, "auto"),  # all boundaries just inside
        (0.3, 129900, 1.0, {}, "approval"),  # risk at auto limit
        (0.0, V, 1.0, {}, "approval"),  # value at threshold
        (0.0, 129900, 0.79, {}, "approval"),  # confidence just below auto
        (0.0, 129900, 0.6, {}, "approval"),  # confidence at escalate limit
        (0.0, 129900, 0.59, {}, "escalate"),  # confidence below escalate limit
        (0.6, 129900, 1.0, {}, "escalate"),  # risk at escalate limit
        (0.0, 129900, 1.0, {"legal_threat": True}, "escalate"),
        (0.0, 129900, 1.0, {"wants_human": True}, "escalate"),
    ],
)
def test_gate_table(
    risk: float, value: int, conf: float, flags: dict[str, bool], route: str
) -> None:
    assert decide_route(risk, value, conf, flags, CFG).route == route


def test_kill_switch_turns_every_auto_into_approval() -> None:
    off = CFG.model_copy(update={"kill_switch": True})
    result = decide_route(0.0, 100, 1.0, {}, off)
    assert result.route == "approval" and result.kill_switch_applied
    assert decide_route(0.7, 100, 1.0, {}, off).route == "escalate"  # humans either way


def test_item_value_prefers_refund_quote() -> None:
    assert item_value_minor(facts(price=1000, refund_quote={"total_minor": 900})) == 900
    f = facts(price=1000)
    f["item"].update(qty_ordered=3, qty_returning=2)
    f["pricing"]["line_discount_minor"] = 300
    assert item_value_minor(f) == (3000 - 300) * 2 // 3


# --- Record -------------------------------------------------------------------------------------


def test_record_contains_everything_needed_to_explain() -> None:
    f = facts(
        policy={
            "eligible": True,
            "policy_version": "2026-10",
            "legal_version": "l",
            "legal_protection": [],
            "trace": [
                {"clause_id": "RET-WINDOW-01", "result": "passed"},
                {"clause_id": "RET-GIFT-01", "result": "not_applicable"},
            ],
        },
        llm_trace={"UNDERSTAND_REQUEST": {"prompt_refs": ["system@1"], "agreement": 1.0}},
    )
    ranked = score_options(["exchange", "refund"], f, CFG, 0.0)
    risk = RiskResult(score=0.0, signals=[])
    conf = confidence(f)
    gate = decide_route(0.0, 129900, conf["overall"], {}, CFG)
    record = build_record(f, ranked, risk, conf, gate, 129900, CFG, "returns-v1")
    assert record["chosen"] == "refund" and record["route"] == "auto"
    assert record["policy"]["applied_clauses"] == ["RET-WINDOW-01"]
    assert record["versions"] == {
        "decision_config": CFG.version,
        "graph": "returns-v1",
        "policy": "2026-10",
        "legal": "l",
        "prompts": {"UNDERSTAND_REQUEST": ["system@1"]},
    }
    assert record["rationale"].startswith("Recommended refund")
    assert "Route auto" in record["rationale"]
