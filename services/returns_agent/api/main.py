"""Dev entrypoint: `uvicorn returns_agent.api.main:app`. Mock adapters until real ones exist."""

from returns_agent.adapters.mock import (
    MockCarrierAdapter,
    MockInventoryAdapter,
    MockNotificationAdapter,
    MockOrderAdapter,
    MockPaymentAdapter,
)
from returns_agent.api.app import configure_logging, create_app
from returns_agent.api.deps import Adapters
from returns_agent.seed.generator import generate

configure_logging()
_seed = generate()
app = create_app(
    Adapters(
        orders=MockOrderAdapter(_seed),
        carrier=MockCarrierAdapter(),
        payment=MockPaymentAdapter(),
        inventory=MockInventoryAdapter(),
        notification=MockNotificationAdapter(),
    )
)
