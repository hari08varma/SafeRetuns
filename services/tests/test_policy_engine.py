from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from returns_agent.config import config_dir
from returns_agent.policy.engine import (
    ItemFacts,
    OrderFacts,
    PolicyDecision,
    PolicyFacts,
    RequestFacts,
    evaluate_policy,
)
from returns_agent.policy.schema import (
    PolicyDoc,
    PolicyError,
    load_policies,
    load_policy,
    select_version,
)

AS_OF = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LEGAL_DOCS, MERCHANT_DOCS = load_policies(config_dir() / "policies")
LEGAL = select_version(LEGAL_DOCS, AS_OF)
MERCHANT = select_version(MERCHANT_DOCS, AS_OF)


def facts(
    *,
    reason: str = "size_fit",
    category: str = "apparel",
    final_sale: bool = False,
    qty_ordered: int = 2,
    qty_returning: int = 1,
    qty_already_returned: int = 0,
    status: str = "delivered",
    days_since_delivery: int | None = 16,
    payment: str = "upi",
    gift: bool = False,
) -> PolicyFacts:
    delivered = (
        AS_OF - timedelta(days=days_since_delivery) if days_since_delivery is not None else None
    )
    return PolicyFacts(
        order=OrderFacts(
            order_id="ORD-1",
            status=status,
            payment_method=payment,
            placed_at=AS_OF - timedelta(days=60),
            delivered_at=delivered,
        ),
        item=ItemFacts(
            sku="SKU-1",
            category=category,
            final_sale=final_sale,
            qty_ordered=qty_ordered,
            qty_returning=qty_returning,
            qty_already_returned=qty_already_returned,
        ),
        request=RequestFacts(reason_category=reason, is_gift=gift),  # type: ignore[arg-type]
    )


def decide(**kwargs: Any) -> PolicyDecision:
    return evaluate_policy(LEGAL, MERCHANT, facts(**kwargs), AS_OF)


def results(decision: PolicyDecision) -> dict[str, str]:
    return {t.clause_id: t.result for t in decision.trace}


# name, facts, eligible, reasons, allowed resolutions, refund methods, evidence, shipping refundable
SCENARIOS: list[
    tuple[str, dict[str, Any], bool, list[str], list[str], list[str], list[str], bool]
] = [
    (
        "size/fit prepaid",
        {},
        True,
        [],
        ["exchange", "refund", "store_credit"],
        ["source", "store_credit"],
        [],
        True,
    ),
    (
        "not delivered",
        {"status": "in_transit", "days_since_delivery": None},
        False,
        ["NOT_DELIVERED"],
        [],
        [],
        [],
        True,
    ),
    ("outside window", {"days_since_delivery": 41}, False, ["OUTSIDE_WINDOW"], [], [], [], True),
    ("too many units", {"qty_returning": 3}, False, ["INVALID_QUANTITY"], [], [], [], True),
    (
        "units already returned",
        {"qty_returning": 1, "qty_ordered": 1, "qty_already_returned": 1},
        False,
        ["INVALID_QUANTITY"],
        [],
        [],
        [],
        True,
    ),
    (
        "innerwear size/fit",
        {"category": "innerwear"},
        False,
        ["NON_RETURNABLE_CATEGORY"],
        [],
        [],
        [],
        True,
    ),
    (
        "innerwear defective (legal)",
        {"category": "innerwear", "reason": "defective"},
        True,
        [],
        ["exchange", "refund", "replacement", "store_credit"],
        ["source", "store_credit"],
        ["photo"],
        True,
    ),
    (
        "final sale change of mind",
        {"final_sale": True, "reason": "changed_mind"},
        False,
        ["FINAL_SALE"],
        [],
        [],
        [],
        False,
    ),
    (
        "final sale damaged (legal)",
        {"final_sale": True, "reason": "damaged"},
        True,
        [],
        ["exchange", "refund", "replacement", "store_credit"],
        ["source", "store_credit"],
        ["photo"],
        True,
    ),
    (
        "change of mind",
        {"reason": "changed_mind"},
        True,
        [],
        ["exchange", "refund", "store_credit"],
        ["source", "store_credit"],
        [],
        False,
    ),
    (
        "gift size/fit",
        {"gift": True},
        True,
        [],
        ["exchange", "store_credit"],
        ["store_credit"],
        [],
        True,
    ),
    (
        "gift damaged (legal adds refund/replacement)",
        {"gift": True, "reason": "damaged"},
        True,
        [],
        ["exchange", "refund", "replacement", "store_credit"],
        ["store_credit"],
        ["photo"],
        True,
    ),
    (
        "cash on delivery",
        {"payment": "cod"},
        True,
        [],
        ["exchange", "refund", "store_credit"],
        ["bank_transfer", "store_credit", "upi"],
        [],
        True,
    ),
    (
        "defective outside window (hard limit)",
        {"reason": "defective", "days_since_delivery": 45},
        False,
        ["OUTSIDE_WINDOW"],
        [],
        [],
        ["photo"],
        True,
    ),
    (
        "wrong item, not as described",
        {"reason": "not_as_described"},
        True,
        [],
        ["exchange", "refund", "replacement", "store_credit"],
        ["source", "store_credit"],
        [],
        True,
    ),
]


@pytest.mark.parametrize(
    ("name", "kw", "eligible", "reasons", "allowed", "methods", "evidence", "shipping"),
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_scenarios(
    name: str,
    kw: dict[str, Any],
    eligible: bool,
    reasons: list[str],
    allowed: list[str],
    methods: list[str],
    evidence: list[str],
    shipping: bool,
) -> None:
    d = decide(**kw)
    assert d.eligible is eligible
    assert d.reason_codes == reasons
    assert d.allowed_resolutions == allowed
    assert d.refund_methods == methods
    assert d.required_evidence == evidence
    assert d.shipping_refundable is shipping


def test_every_clause_is_covered_both_ways() -> None:
    seen: dict[str, set[str]] = {}
    for _, kw, *_ in SCENARIOS:
        for clause, result in results(decide(**kw)).items():
            seen.setdefault(clause, set()).add(result)
    for doc in (LEGAL, MERCHANT):
        for rule in doc.rules:
            outcomes = seen.get(rule.clause_id, set())
            assert len(outcomes) >= 2, f"{rule.clause_id} only saw {outcomes}"
            if rule.require is not None:
                assert "failed" in outcomes, f"{rule.clause_id} never failed in any scenario"


def test_trace_is_complete_and_golden() -> None:
    d = decide(category="innerwear", reason="defective")
    assert [t.clause_id for t in d.trace] == [r.clause_id for r in LEGAL.rules + MERCHANT.rules]
    assert results(d) == {
        "LEGAL-IN-DEFECT-01": "applied",
        "RET-DELIVERED-01": "passed",
        "RET-QTY-01": "passed",
        "RET-WINDOW-01": "passed",
        "RET-NONRET-01": "overridden",
        "RET-FINALSALE-01": "not_applicable",
        "RET-REPLACE-01": "not_applicable",
        "RET-EVIDENCE-01": "applied",
        "RET-CHANGEDMIND-01": "not_applicable",
        "RET-GIFT-01": "not_applicable",
        "RET-REFUND-PREPAID-01": "applied",
        "RET-COD-01": "not_applicable",
    }
    window = next(t for t in d.trace if t.clause_id == "RET-WINDOW-01")
    assert window.inputs == {"days_since_delivery": 16, "return_window_days": 30}
    assert d.legal_protection == ["LEGAL-IN-DEFECT-01"]
    assert (d.legal_version, d.policy_version) == ("legal-in-2020", "2026-10")


def test_window_remaining() -> None:
    assert decide(days_since_delivery=16).window_remaining_days == 14
    assert decide(days_since_delivery=40).window_remaining_days == 0
    assert decide(status="in_transit", days_since_delivery=None).window_remaining_days is None


def test_deterministic() -> None:
    assert decide(reason="damaged", gift=True) == decide(reason="damaged", gift=True)


def test_legal_waives_merchant_fees() -> None:
    merchant = MERCHANT.model_copy(deep=True)
    fee_rule = merchant.rules[-1].model_copy(deep=True)
    fee_rule.clause_id, fee_rule.applies_to = "RET-FEE-TEST", True
    fee_rule.effects.restocking_fee_pct, fee_rule.effects.shipping_refundable = 15, False
    fee_rule.effects.refund_methods = None
    merchant.rules.append(fee_rule)
    normal = evaluate_policy(LEGAL, merchant, facts(reason="changed_mind"), AS_OF)
    protected = evaluate_policy(LEGAL, merchant, facts(reason="defective"), AS_OF)
    assert (normal.restocking_fee_pct, normal.shipping_refundable) == (15, False)
    assert (protected.restocking_fee_pct, protected.shipping_refundable) == (0, True)


def test_version_in_force_on_order_date() -> None:
    old = MERCHANT.model_copy(
        update={"version": "2025-01", "effective_from": datetime(2025, 1, 1, tzinfo=UTC)}
    )
    new = MERCHANT.model_copy(
        update={"version": "2026-12", "effective_from": datetime(2026, 12, 1, tzinfo=UTC)}
    )
    docs = [old, MERCHANT, new]
    assert select_version(docs, datetime(2025, 6, 1, tzinfo=UTC)).version == "2025-01"
    assert select_version(docs, datetime(2026, 11, 1, tzinfo=UTC)).version == "2026-10"
    assert select_version(docs, datetime(2027, 1, 1, tzinfo=UTC)).version == "2026-12"
    with pytest.raises(PolicyError):
        select_version(docs, datetime(2024, 1, 1, tzinfo=UTC))


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "p.yaml"
    path.write_text(body)
    return path


HEADER = "version: t\nlayer: merchant\neffective_from: 2026-01-01T00:00:00Z\n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            HEADER + "return_window_days: 30\nrules:\n"
            "  - {clause_id: A, text: a}\n  - {clause_id: A, text: b}\n",
            "duplicate clause ids",
        ),
        (
            HEADER
            + "return_window_days: 30\nrules:\n  - {clause_id: A, text: a, require: false}\n",
            "need a reason_code",
        ),
        (
            HEADER + "return_window_days: 30\nrules:\n"
            "  - {clause_id: A, text: a, effects: {waive_fees: true}}\n",
            "only legal rules",
        ),
        (HEADER + "rules: []\n", "return_window_days"),
    ],
)
def test_loader_rejects_invalid_policies(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(PolicyError, match=message):
        load_policy(_write(tmp_path, body))


def test_shipped_policies_load() -> None:
    assert isinstance(MERCHANT, PolicyDoc) and MERCHANT.return_window_days == 30
    assert LEGAL.layer == "legal" and len(LEGAL.rules) == 1
