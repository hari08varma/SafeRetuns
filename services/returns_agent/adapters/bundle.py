from dataclasses import dataclass

from returns_agent.adapters.base import (
    CarrierAdapter,
    InventoryAdapter,
    NotificationAdapter,
    OrderAdapter,
    PaymentAdapter,
)


@dataclass
class Adapters:
    orders: OrderAdapter
    carrier: CarrierAdapter
    payment: PaymentAdapter
    inventory: InventoryAdapter
    notification: NotificationAdapter
