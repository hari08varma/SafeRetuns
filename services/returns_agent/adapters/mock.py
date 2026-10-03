"""In-memory adapters with deterministic failure injection, for dev and tests.

faults = FaultPlan()
faults.inject("payment.refund", "timeout", "ok")   # 1st call times out, 2nd succeeds
"""

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from returns_agent.adapters.base import (
    AdapterError,
    AdapterTimeout,
    OrderInfo,
    OrderLine,
    PickupBooking,
    RefundResult,
)
from returns_agent.seed.generator import SeedData

Outcome = str | tuple[str, float]  # "ok" | "error" | "timeout" | ("delay", seconds)


@dataclass
class FaultPlan:
    sleep: Callable[[float], None] = time.sleep
    _plans: dict[str, list[Outcome]] = field(default_factory=lambda: defaultdict(list))
    calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def inject(self, operation: str, *outcomes: Outcome) -> None:
        self._plans[operation].extend(outcomes)

    def apply(self, operation: str) -> None:
        self.calls[operation] += 1
        plan = self._plans[operation]
        outcome = plan.pop(0) if plan else "ok"
        if isinstance(outcome, tuple):
            self.sleep(outcome[1])
        elif outcome == "error":
            raise AdapterError(f"{operation}: injected error")
        elif outcome == "timeout":
            raise AdapterTimeout(f"{operation}: injected timeout")


class MockOrderAdapter:
    def __init__(self, seed: SeedData, faults: FaultPlan | None = None) -> None:
        self.faults = faults or FaultPlan()
        self._orders = {
            o.id: OrderInfo(
                order_id=o.id,
                customer_id=o.customer_id,
                status=o.status,
                payment_method=o.payment_method,
                total_minor=o.total_minor,
                delivered_at=o.delivered_at.isoformat() if o.delivered_at else None,
                lines=[
                    OrderLine(
                        i.id, i.sku, i.qty, i.unit_price_minor, i.discount_alloc_minor, i.final_sale
                    )
                    for i in o.items
                ],
            )
            for o in seed.orders
        }
        self._replacements: dict[str, str] = {}
        self.cancelled: dict[str, str] = {}

    def get_order(self, order_id: str) -> OrderInfo:
        self.faults.apply("order.get")
        if order_id not in self._orders:
            raise AdapterError(f"order {order_id} not found")
        return self._orders[order_id]

    def list_orders(self, customer_id: str) -> list[OrderInfo]:
        self.faults.apply("order.list")
        return [o for o in self._orders.values() if o.customer_id == customer_id]

    def create_replacement(self, order_id: str, item_id: str, idempotency_key: str) -> str:
        if idempotency_key not in self._replacements:
            self.faults.apply("order.create_replacement")
            self._replacements[idempotency_key] = f"REPL-{len(self._replacements) + 1:05d}"
        return self._replacements[idempotency_key]

    def cancel_order(self, order_ref: str, idempotency_key: str) -> None:
        if idempotency_key not in self.cancelled:
            self.faults.apply("order.cancel")
            self.cancelled[idempotency_key] = order_ref


class MockCarrierAdapter:
    def __init__(
        self, unserviceable: set[str] | None = None, faults: FaultPlan | None = None
    ) -> None:
        self.faults = faults or FaultPlan()
        self.unserviceable = unserviceable or set()
        self._bookings: dict[str, PickupBooking] = {}
        self.events: dict[str, list[str]] = {}

    def is_serviceable(self, pincode: str) -> bool:
        self.faults.apply("carrier.serviceable")
        return pincode not in self.unserviceable

    def schedule_pickup(self, case_id: str, pincode: str, idempotency_key: str) -> PickupBooking:
        if idempotency_key in self._bookings:
            return self._bookings[idempotency_key]
        self.faults.apply("carrier.schedule_pickup")
        if pincode in self.unserviceable:
            raise AdapterError(f"pincode {pincode} not serviceable")
        booking = PickupBooking(awb=f"AWB{len(self._bookings) + 1:08d}", slot="next-day 10-14")
        self._bookings[idempotency_key] = booking
        self.events[booking.awb] = ["pickup_scheduled"]
        return booking

    def track(self, awb: str) -> list[str]:
        self.faults.apply("carrier.track")
        return list(self.events.get(awb, []))


class MockPaymentAdapter:
    def __init__(self, faults: FaultPlan | None = None) -> None:
        self.faults = faults or FaultPlan()
        self.refunds: dict[str, tuple[str, int, str]] = {}

    def refund(
        self, order_id: str, amount_minor: int, method: str, idempotency_key: str
    ) -> RefundResult:
        if amount_minor <= 0:
            raise AdapterError("refund amount must be positive")
        if idempotency_key not in self.refunds:
            self.faults.apply("payment.refund")
            self.refunds[idempotency_key] = (order_id, amount_minor, method)
        index = list(self.refunds).index(idempotency_key) + 1
        return RefundResult(gateway_ref=f"RF{index:08d}", status="succeeded")


class MockInventoryAdapter:
    def __init__(
        self, stock: dict[str, int] | None = None, faults: FaultPlan | None = None
    ) -> None:
        self.faults = faults or FaultPlan()
        self.stock = dict(stock or {})
        self._reservations: dict[str, str] = {}
        self._reserved: dict[str, tuple[str, int]] = {}
        self.released: set[str] = set()

    def in_stock(self, sku: str, qty: int = 1) -> bool:
        self.faults.apply("inventory.in_stock")
        return self.stock.get(sku, 0) >= qty

    def reserve(self, sku: str, qty: int, idempotency_key: str) -> str:
        if idempotency_key in self._reservations:
            return self._reservations[idempotency_key]
        self.faults.apply("inventory.reserve")
        if self.stock.get(sku, 0) < qty:
            raise AdapterError(f"{sku} out of stock")
        self.stock[sku] -= qty
        self._reservations[idempotency_key] = f"RSV-{len(self._reservations) + 1:05d}"
        self._reserved[self._reservations[idempotency_key]] = (sku, qty)
        return self._reservations[idempotency_key]

    def release(self, reservation_id: str, idempotency_key: str) -> None:
        if idempotency_key in self.released or reservation_id not in self._reserved:
            return
        self.faults.apply("inventory.release")
        sku, qty = self._reserved.pop(reservation_id)
        self.stock[sku] = self.stock.get(sku, 0) + qty
        self.released.add(idempotency_key)


class MockNotificationAdapter:
    def __init__(self, faults: FaultPlan | None = None) -> None:
        self.faults = faults or FaultPlan()
        self.sent: list[dict[str, object]] = []

    def send(self, channel: str, to: str, template: str, data: dict[str, str]) -> str:
        self.faults.apply("notification.send")
        self.sent.append({"channel": channel, "to": to, "template": template, "data": data})
        return f"MSG-{len(self.sent):06d}"
