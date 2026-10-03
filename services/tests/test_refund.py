import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from returns_agent.policy.refund import (
    Payment,
    RefundError,
    RefundLine,
    compute_refund,
    line_paid,
    refundable_for_line,
)

UPI = [Payment(method="upi", amount_minor=10**9)]


def line(
    unit: int = 129900, qty: int = 1, discount: int = 0, ret: int = 1, already: int = 0
) -> RefundLine:
    return RefundLine(
        unit_price_minor=unit,
        qty_ordered=qty,
        line_discount_minor=discount,
        qty_returning=ret,
        qty_already_returned=already,
    )


def test_full_line_refund_includes_coupon_share() -> None:
    r = compute_refund([line(unit=149900, discount=14990)], UPI)
    assert r.items_minor == 134910 and r.total_minor == 134910


def test_partial_returns_sum_to_full_line() -> None:
    first = refundable_for_line(line(unit=33333, qty=3, discount=100, ret=1))
    second = refundable_for_line(line(unit=33333, qty=3, discount=100, ret=2, already=1))
    full = refundable_for_line(line(unit=33333, qty=3, discount=100, ret=3))
    assert first + second == full == 33333 * 3 - 100


def test_restocking_fee_rounds_in_customers_favour() -> None:
    r = compute_refund([line(unit=999)], UPI, restocking_fee_pct=10)
    assert r.restocking_fee_minor == 99 and r.total_minor == 900


def test_shipping_only_when_whole_order_and_refundable() -> None:
    lines = [line()]
    assert compute_refund(lines, UPI, shipping_fee_minor=4900).shipping_minor == 0
    assert (
        compute_refund(lines, UPI, shipping_fee_minor=4900, returns_whole_order=True).shipping_minor
        == 4900
    )
    assert (
        compute_refund(
            lines, UPI, shipping_fee_minor=4900, returns_whole_order=True, shipping_refundable=False
        ).shipping_minor
        == 0
    )


def test_capped_by_amount_paid_minus_earlier_refunds() -> None:
    paid = [Payment(method="card", amount_minor=100000)]
    r = compute_refund([line(unit=129900)], paid, already_refunded_minor=40000)
    assert r.total_minor == 60000


def test_cod_goes_to_chosen_destination() -> None:
    r = compute_refund(
        [line(unit=50000)], [Payment(method="cod", amount_minor=50000)], cod_destination="upi"
    )
    assert [(a.method, a.amount_minor) for a in r.allocations] == [("upi", 50000)]


def test_split_payment_allocation_is_proportional_and_exact() -> None:
    payments = [
        Payment(method="card", amount_minor=70000),
        Payment(method="wallet", amount_minor=30001),
    ]
    r = compute_refund([line(unit=100001)], payments)
    amounts = {a.method: a.amount_minor for a in r.allocations}
    assert sum(amounts.values()) == r.total_minor == 100001
    assert amounts == {"card": 70000, "wallet": 30001}


def test_store_credit_destination() -> None:
    r = compute_refund([line()], UPI, destination="store_credit")
    assert [(a.method, a.amount_minor) for a in r.allocations] == [("store_credit", 129900)]


@pytest.mark.parametrize(
    ("kwargs", "lines"),
    [
        ({}, []),
        ({}, [line(qty=1, ret=2)]),
        ({"restocking_fee_pct": 101}, [line()]),
        ({}, [line(unit=100, qty=1, discount=200)]),
    ],
)
def test_invalid_inputs(kwargs: dict[str, int], lines: list[RefundLine]) -> None:
    with pytest.raises(RefundError):
        compute_refund(lines, UPI, **kwargs)  # type: ignore[arg-type]


# --- Properties ---------------------------------------------------------------------------


@st.composite
def lines_strategy(draw: st.DrawFn) -> RefundLine:
    unit = draw(st.integers(0, 10_000_000))
    qty = draw(st.integers(1, 6))
    discount = draw(st.integers(0, unit * qty))
    already = draw(st.integers(0, qty - 1))
    ret = draw(st.integers(1, qty - already))
    return line(unit=unit, qty=qty, discount=discount, ret=ret, already=already)


@settings(max_examples=300)
@given(
    st.lists(lines_strategy(), min_size=1, max_size=4),
    st.integers(0, 100),
    st.lists(
        st.tuples(st.sampled_from(["card", "upi", "wallet", "cod"]), st.integers(0, 50_000_000)),
        min_size=1,
        max_size=3,
    ),
)
def test_refund_never_exceeds_paid_and_allocations_sum(
    lines: list[RefundLine], fee_pct: int, raw_payments: list[tuple[str, int]]
) -> None:
    payments = [Payment(method=m, amount_minor=a) for m, a in raw_payments]
    r = compute_refund(lines, payments, restocking_fee_pct=fee_pct)
    paid_for_returned = sum(
        line_paid(ln) * (ln.qty_already_returned + ln.qty_returning) // ln.qty_ordered
        - line_paid(ln) * ln.qty_already_returned // ln.qty_ordered
        for ln in lines
    )
    assert 0 <= r.total_minor <= paid_for_returned
    assert r.total_minor <= sum(p.amount_minor for p in payments)
    assert sum(a.amount_minor for a in r.allocations) == r.total_minor
    assert compute_refund(lines, payments, restocking_fee_pct=fee_pct) == r  # deterministic


@settings(max_examples=300)
@given(st.integers(0, 10_000_000), st.integers(1, 8), st.data())
def test_any_sequence_of_partial_returns_sums_to_line_paid(
    unit: int, qty: int, data: st.DataObject
) -> None:
    discount = data.draw(st.integers(0, unit * qty))
    returned, total = 0, 0
    while returned < qty:
        step = data.draw(st.integers(1, qty - returned))
        total += refundable_for_line(
            line(unit=unit, qty=qty, discount=discount, ret=step, already=returned)
        )
        returned += step
    assert total == unit * qty - discount
