"""Four delivered orders for every customer account, so each account has something to return.

Each order is chosen to show a different path through the agent:
  1. Running Sneakers (₹3,499)        size or fit → exchange or refund, decided automatically
  2. Wireless Earbuds (₹2,999)        damaged or defective → photo, then replacement
  3. Cotton Kurta + 2 Graphic T-shirts changed mind → refund to the original payment
  4. Smartwatch (₹5,999)              above the ₹5,000 auto limit → human approval

Applied when an account is created and, at API start-up, to existing accounts that have no
orders. Run by hand:
  uv run python -m returns_agent.seed.customer_orders            # every account without orders
  uv run python -m returns_agent.seed.customer_orders --phone 9391182021
"""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.db.models import Customer, Order, OrderItem, Product
from returns_agent.db.session import get_engine
from returns_agent.security.pii import blind_index
from returns_agent.seed.generator import generate

# (lines [(sku, qty)], days since delivery, payment method)
ORDERS: list[tuple[list[tuple[str, int]], int, str]] = [
    ([("SNK-9", 1)], 2, "upi"),
    ([("EBD-white", 1)], 1, "upi"),
    ([("KUR-M", 1), ("TSH-L", 2)], 6, "cod"),
    ([("SWT-black", 1)], 4, "card"),
]


def ensure_catalog(session: Session) -> int:
    """Adds any catalogue products that are missing (idempotent)."""
    known = set(session.scalars(select(Product.sku)))
    new = [p for p in generate(seed=1, customers=1).products if p.sku not in known]
    session.add_all(
        Product(
            sku=p.sku,
            title=p.title,
            category=p.category,
            variant=p.variant,
            price_minor=p.price_minor,
            returnable=p.returnable,
            image_uris=[p.image_uri],
        )
        for p in new
    )
    session.flush()
    return len(new)


def give_orders(session: Session, customer: Customer) -> int:
    """Creates the four orders for a customer who has none. Returns how many were created.
    The caller commits."""
    if session.scalar(select(Order.id).where(Order.customer_id == customer.id).limit(1)):
        return 0
    ensure_catalog(session)
    prices = {p.sku: p.price_minor for p in session.scalars(select(Product))}
    prefix = f"VAP-{customer.id.hex[:6].upper()}-"
    now = datetime.now(UTC)
    for n, (lines, days, payment) in enumerate(ORDERS, start=1):
        ref = f"{prefix}{n:02d}"
        order = Order(
            external_id=ref,
            customer_id=customer.id,
            placed_at=now - timedelta(days=days + 3),
            delivered_at=now - timedelta(days=days),
            status="delivered",
            payment_method=payment,
            coupon_minor=0,
            total_minor=sum(prices[sku] * qty for sku, qty in lines),
        )
        order.items = [
            OrderItem(
                external_id=f"{ref}-{i}",
                sku=sku,
                qty=qty,
                unit_price_minor=prices[sku],
                discount_alloc_minor=0,
                final_sale=False,
            )
            for i, (sku, qty) in enumerate(lines, start=1)
        ]
        session.add(order)
    session.flush()
    return len(ORDERS)


def backfill(session: Session) -> int:
    """Gives the orders to every existing account that has none. Returns accounts updated."""
    has_orders = select(Order.customer_id).distinct()
    customers = session.scalars(select(Customer).where(Customer.id.not_in(has_orders))).all()
    updated = sum(1 for c in customers if give_orders(session, c))
    session.commit()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phone", help="only this account, e.g. 9391182021 or +919391182021")
    args = parser.parse_args()
    with Session(get_engine()) as session:
        if args.phone:
            digits = "".join(c for c in args.phone if c.isdigit())
            phone = f"+91{digits}" if len(digits) == 10 else f"+{digits}"
            customer = session.scalar(
                select(Customer).where(Customer.phone_index == blind_index(phone))
            )
            if customer is None:
                raise SystemExit(f"no account with {phone}: sign in once first")
            created = give_orders(session, customer)
            session.commit()
            print(f"{created} orders created" if created else "account already has orders")
        else:
            print(f"orders added to {backfill(session)} accounts")


if __name__ == "__main__":
    main()
