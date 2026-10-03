import secrets

from returns_agent.adapters.bundle import Adapters
from returns_agent.adapters.mock import (
    MockCarrierAdapter,
    MockInventoryAdapter,
    MockNotificationAdapter,
    MockOrderAdapter,
    MockPaymentAdapter,
)
from returns_agent.seed.generator import generate


def mock_adapters() -> Adapters:
    """Development adapters until real integrations exist. In-memory state is per process,
    so the API and the worker each hold their own copy."""
    seed = generate()
    return Adapters(
        orders=MockOrderAdapter(seed),
        carrier=MockCarrierAdapter(
            awb_prefix=f"AWB{secrets.token_hex(2).upper()}-"
        ),  # unique per run
        payment=MockPaymentAdapter(),
        inventory=MockInventoryAdapter({p.sku: 20 for p in seed.products}),
        notification=MockNotificationAdapter(),
    )
