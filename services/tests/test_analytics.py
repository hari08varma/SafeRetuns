"""Analytics summary runs on real data and turns repeated causes into product fixes."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.analytics.summary import summary
from returns_agent.db.models import Customer, Order, OrderItem, Refund, ReturnCase, ReturnItem
from tests.conftest import needs_db


@needs_db
def test_summary_with_prevention_insight(seeded_db: Session) -> None:
    db = seeded_db
    customer = db.scalars(select(Customer)).first()
    assert customer is not None
    now = datetime.now(UTC)
    for i, reason in enumerate(["size_fit", "size_fit", "size_fit", "damaged"]):
        order = Order(
            external_id=f"AN-{i}",
            customer_id=customer.id,
            placed_at=now - timedelta(days=5),
            delivered_at=now - timedelta(days=2),
            status="delivered",
            payment_method="upi",
            coupon_minor=0,
            total_minor=129900,
        )
        order.items = [
            OrderItem(
                external_id=f"AN-{i}-1",
                sku="KUR-M",
                qty=1,
                unit_price_minor=129900,
                discount_alloc_minor=0,
                final_sale=False,
            )
        ]
        db.add(order)
        db.flush()
        case = ReturnCase(
            customer_id=customer.id,
            order_id=order.id,
            graph_version="returns-v1",
            status="closed",
            route="auto",
            closed_at=now,
        )
        db.add(case)
        db.flush()
        db.add(
            ReturnItem(
                case_id=case.id, order_item_id=order.items[0].id, qty=1, reason_category=reason
            )
        )
        db.add(
            Refund(
                case_id=case.id,
                amount_minor=129900,
                method="source",
                idempotency_key=f"an-{uuid.uuid4()}",
                status="succeeded",
            )
        )
    db.commit()

    s = summary(db, days=30)
    assert s["cases"] == 4 and s["by_reason"]["size_fit"] == 3
    assert s["resolutions"]["keep_item_refund"] == 4  # refunded, nothing collected
    assert s["auto_resolution_rate"] == 1.0
    kurta = next(i for i in s["insights"] if i["family"] == "KUR")
    assert kurta["problem"] == "Size and fit" and kurta["share"] == 0.75
