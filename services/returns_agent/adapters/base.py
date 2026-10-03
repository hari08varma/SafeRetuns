"""External-system interfaces. Business code depends only on these, never on vendors."""

from dataclasses import dataclass, field
from typing import Protocol


class AdapterError(Exception):
    """The external system returned an error."""


class AdapterTimeout(AdapterError):
    """The external system did not answer in time."""


@dataclass(frozen=True)
class OrderLine:
    item_id: str
    sku: str
    qty: int
    unit_price_minor: int
    discount_alloc_minor: int
    final_sale: bool


@dataclass(frozen=True)
class OrderInfo:
    order_id: str
    customer_id: str
    status: str
    payment_method: str
    total_minor: int
    delivered_at: str | None
    lines: list[OrderLine] = field(default_factory=list)


@dataclass(frozen=True)
class PickupBooking:
    awb: str
    slot: str


@dataclass(frozen=True)
class RefundResult:
    gateway_ref: str
    status: str  # "succeeded" | "pending"


class OrderAdapter(Protocol):
    def get_order(self, order_id: str) -> OrderInfo: ...
    def list_orders(self, customer_id: str) -> list[OrderInfo]: ...
    def create_replacement(self, order_id: str, item_id: str, idempotency_key: str) -> str: ...
    def cancel_order(self, order_ref: str, idempotency_key: str) -> None: ...


class CarrierAdapter(Protocol):
    def is_serviceable(self, pincode: str) -> bool: ...
    def schedule_pickup(
        self, case_id: str, pincode: str, idempotency_key: str
    ) -> PickupBooking: ...
    def track(self, awb: str) -> list[str]: ...


class PaymentAdapter(Protocol):
    def refund(
        self, order_id: str, amount_minor: int, method: str, idempotency_key: str
    ) -> RefundResult: ...


class InventoryAdapter(Protocol):
    def in_stock(self, sku: str, qty: int = 1) -> bool: ...
    def reserve(self, sku: str, qty: int, idempotency_key: str) -> str: ...
    def release(self, reservation_id: str, idempotency_key: str) -> None: ...


class NotificationAdapter(Protocol):
    def send(self, channel: str, to: str, template: str, data: dict[str, str]) -> str: ...
