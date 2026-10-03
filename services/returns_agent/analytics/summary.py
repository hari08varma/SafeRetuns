"""Returns analytics for the dashboard, computed from the database (no caching layer needed
at this scale). Includes prevention insights: which products to fix, and why."""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from returns_agent.db.models import (
    AuditEvent,
    Exchange,
    Goodwill,
    Order,
    OrderItem,
    Product,
    Refund,
    ReplacementOrder,
    ReturnCase,
    ReturnItem,
    Shipment,
)

# Prevention rules: a share of a product's returns with one cause points to one fix.
MIN_RETURNS_FOR_INSIGHT = 3
INSIGHT_RULES = [
    ("size_fit", 0.4, "Size and fit", "Review the size chart and add fit notes or model sizes."),
    (
        "not_as_described",
        0.3,
        "Listing mismatch",
        "Update photos, colour and description so they match the product.",
    ),
    ("damaged", 0.3, "Damaged in transit", "Check packaging and the courier for this product."),
    ("defective", 0.3, "Quality issue", "Raise a quality check with the supplier."),
    ("wrong_item", 0.2, "Picking error", "Check warehouse picking and labelling for this SKU."),
]


def summary(session: Session, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    cases = session.scalars(select(ReturnCase).where(ReturnCase.created_at >= since)).all()
    ids = [c.id for c in cases]

    lines = session.execute(
        select(
            ReturnItem.reason_category,
            ReturnItem.qty,
            OrderItem.sku,
            Product.title,
            Product.category,
        )
        .join(OrderItem, OrderItem.id == ReturnItem.order_item_id)
        .join(Product, Product.sku == OrderItem.sku)
        .where(ReturnItem.case_id.in_(ids))
    ).all()
    by_reason = Counter((r or "not_stated") for r, *_ in lines)
    by_category = Counter(category for *_, category in lines)

    # Per product family (e.g. all sizes of one kurta), for return rates and insights.
    families: dict[str, dict[str, Any]] = defaultdict(lambda: {"returns": 0, "reasons": Counter()})
    for reason, qty, sku, title, _ in lines:
        fam = families[sku.rsplit("-", 1)[0]]
        fam["title"], fam["returns"] = title, fam["returns"] + qty
        fam["reasons"][reason or "not_stated"] += qty
    family = func.split_part(OrderItem.sku, literal_column("'-'"), 1).label("family")
    sold: dict[str, int] = dict(
        session.execute(
            select(family, func.sum(OrderItem.qty))
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.placed_at >= since - timedelta(days=30))
            .group_by(family)
        ).all()
    )
    products = sorted(
        (
            {
                "family": fam_id,
                "title": f["title"],
                "returns": f["returns"],
                "units_sold": int(sold.get(fam_id, 0) or 0),
                "return_rate": round(f["returns"] / sold[fam_id], 3) if sold.get(fam_id) else None,
                "top_reason": f["reasons"].most_common(1)[0][0],
            }
            for fam_id, f in families.items()
        ),
        key=lambda p: -p["returns"],
    )
    insights = []
    for fam_id, f in families.items():
        if f["returns"] < MIN_RETURNS_FOR_INSIGHT:
            continue
        for reason, threshold, problem, action in INSIGHT_RULES:
            share = f["reasons"][reason] / f["returns"]
            if share >= threshold:
                insights.append(
                    {
                        "family": fam_id,
                        "title": f["title"],
                        "problem": problem,
                        "share": round(share, 2),
                        "returns": f["returns"],
                        "action": action,
                    }
                )
    insights.sort(key=lambda i: (-i["returns"], -i["share"]))

    refunds = session.execute(
        select(Refund.case_id, Refund.amount_minor, Refund.method).where(
            Refund.case_id.in_(ids), Refund.status == "succeeded"
        )
    ).all()
    collected = set(session.scalars(select(Shipment.case_id).where(Shipment.case_id.in_(ids))))
    exchanges = set(
        session.scalars(
            select(Exchange.case_id).where(
                Exchange.case_id.in_(ids), Exchange.status != "cancelled"
            )
        )
    )
    replacements = set(
        session.scalars(
            select(ReplacementOrder.case_id).where(
                ReplacementOrder.case_id.in_(ids), ReplacementOrder.status != "cancelled"
            )
        )
    )
    keep_item = {cid for cid, _, _ in refunds if cid not in collected}
    executed = exchanges | replacements | {cid for cid, _, _ in refunds}
    closed = [c for c in cases if c.status == "closed"]
    auto_resolved = [c for c in closed if c.route == "auto" and c.id in executed]
    resolution_hours = [
        (c.closed_at - c.created_at).total_seconds() / 3600
        for c in closed
        if c.closed_at and c.id in executed
    ]
    store_credit = sum(a for _, a, m in refunds if m == "store_credit")
    item_value = dict(
        session.execute(
            select(ReturnItem.case_id, OrderItem.unit_price_minor * ReturnItem.qty)
            .join(OrderItem, OrderItem.id == ReturnItem.order_item_id)
            .where(ReturnItem.case_id.in_(ids))
        ).all()
    )
    breaches = session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.action.in_(("sla.breached", "queue.sla_breached")),
            AuditEvent.created_at >= since,
        )
    )
    goodwill = session.scalar(
        select(func.coalesce(func.sum(Goodwill.amount_minor), 0)).where(
            Goodwill.case_id.in_(ids), Goodwill.status != "failed"
        )
    )
    total_orders = session.scalar(select(func.count()).select_from(Order)) or 0

    return {
        "days": days,
        "orders": total_orders,
        "cases": len(cases),
        "open": sum(c.status == "waiting" for c in cases),
        "escalated": sum(c.status == "escalated" for c in cases),
        "closed": len(closed),
        "routes": dict(Counter(c.route or "not_reached" for c in cases)),
        "auto_resolution_rate": round(len(auto_resolved) / len(closed), 3) if closed else None,
        "avg_resolution_hours": round(sum(resolution_hours) / len(resolution_hours), 1)
        if resolution_hours
        else None,
        "resolutions": {
            "refund": len({cid for cid, _, m in refunds if m != "store_credit"} - keep_item),
            "keep_item_refund": len(keep_item),
            "store_credit": len({cid for cid, _, m in refunds if m == "store_credit"}),
            "exchange": len(exchanges),
            "replacement": len(replacements),
        },
        "money": {
            "refunded_minor": sum(a for _, a, m in refunds if m != "store_credit"),
            "store_credit_minor": store_credit,
            "kept_by_exchange_minor": sum(item_value.get(c, 0) for c in exchanges | replacements),
            "reverse_shipping_avoided": len(keep_item),
            "goodwill_minor": int(goodwill or 0),
        },
        "by_reason": dict(by_reason.most_common()),
        "by_category": dict(by_category.most_common()),
        "top_products": products[:10],
        "insights": insights[:10],
        "sla_breaches": int(breaches or 0),
    }
