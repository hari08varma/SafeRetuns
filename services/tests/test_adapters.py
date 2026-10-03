import pytest

from returns_agent.adapters.base import AdapterError, AdapterTimeout
from returns_agent.adapters.mock import (
    FaultPlan,
    MockCarrierAdapter,
    MockInventoryAdapter,
    MockOrderAdapter,
    MockPaymentAdapter,
)
from returns_agent.seed.generator import generate


def test_fault_plan_sequence_is_deterministic() -> None:
    slept: list[float] = []
    faults = FaultPlan(sleep=slept.append)
    faults.inject("payment.refund", "timeout", "error", ("delay", 2.5), "ok")
    pay = MockPaymentAdapter(faults)
    with pytest.raises(AdapterTimeout):
        pay.refund("O1", 100, "upi", "k1")
    with pytest.raises(AdapterError):
        pay.refund("O1", 100, "upi", "k1")
    assert pay.refund("O1", 100, "upi", "k1").status == "succeeded"  # after the delay
    assert slept == [2.5]
    assert faults.calls["payment.refund"] == 3


def test_refund_is_idempotent() -> None:
    pay = MockPaymentAdapter()
    first = pay.refund("O1", 129900, "upi", "case-1:refund:item-1")
    again = pay.refund("O1", 129900, "upi", "case-1:refund:item-1")
    assert first == again
    assert len(pay.refunds) == 1


def test_refund_rejects_non_positive_amount() -> None:
    with pytest.raises(AdapterError):
        MockPaymentAdapter().refund("O1", 0, "upi", "k")


def test_pickup_idempotent_and_serviceability() -> None:
    carrier = MockCarrierAdapter(unserviceable={"999999"})
    booking = carrier.schedule_pickup("c1", "500081", "c1:pickup")
    assert carrier.schedule_pickup("c1", "500081", "c1:pickup") == booking
    assert carrier.track(booking.awb) == ["pickup_scheduled"]
    assert not carrier.is_serviceable("999999")
    with pytest.raises(AdapterError):
        carrier.schedule_pickup("c2", "999999", "c2:pickup")


def test_inventory_reserve() -> None:
    inv = MockInventoryAdapter({"KUR-L": 1})
    assert inv.in_stock("KUR-L") and not inv.in_stock("KUR-XL")
    rsv = inv.reserve("KUR-L", 1, "c1:exchange")
    assert inv.reserve("KUR-L", 1, "c1:exchange") == rsv  # retry does not double-reserve
    assert not inv.in_stock("KUR-L")
    with pytest.raises(AdapterError):
        inv.reserve("KUR-L", 1, "c2:exchange")


def test_order_adapter_reads_seed() -> None:
    seed = generate(seed=1, customers=3)
    orders = MockOrderAdapter(seed)
    first = seed.orders[0]
    info = orders.get_order(first.id)
    assert info.total_minor == first.total_minor and len(info.lines) == len(first.items)
    assert all(o.customer_id == first.customer_id for o in orders.list_orders(first.customer_id))
    with pytest.raises(AdapterError):
        orders.get_order("missing")
    ref = orders.create_replacement(first.id, info.lines[0].item_id, "c1:replacement")
    assert orders.create_replacement(first.id, info.lines[0].item_id, "c1:replacement") == ref
