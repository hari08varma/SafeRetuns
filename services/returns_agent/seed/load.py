"""Load generated seed data into the database (PII encrypted) and create demo staff.

Usage: uv run python -m returns_agent.seed.load --staff-password '<min 12 chars>'
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.db.models import Address, Customer, Order, OrderItem, Product, StaffUser
from returns_agent.db.session import get_engine
from returns_agent.security.pii import address_index, blind_index, encrypt
from returns_agent.security.tokens import STAFF_ROLES, hash_password
from returns_agent.seed.generator import SeedData, generate

AUTHORITY_LIMITS = {"agent": 200_000, "approver": 2_500_000, "admin": 2_500_000}


def _address(customer_id: str, city: str, pincode: str) -> str:
    return f"House {customer_id.rsplit('-', 1)[-1]}, {city} {pincode}"


def load(session: Session, data: SeedData, staff_password: str | None = None) -> bool:
    """Returns False (and changes nothing) if seed data is already present."""
    if session.scalar(select(Customer.id).limit(1)) is not None:
        return False
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
        for p in data.products
    )
    customers: dict[str, Customer] = {}
    for c in data.customers:
        customer = Customer(
            external_id=c.id,
            name_enc=encrypt(c.name),
            phone_enc=encrypt(c.phone),
            phone_index=blind_index(c.phone),
            email_enc=encrypt(c.email),
            email_index=blind_index(c.email),
            tier=c.tier,
            account_age_days=c.account_age_days,
        )
        customers[c.id] = customer
        session.add(customer)
    session.flush()
    for c in data.customers:
        session.add(
            Address(
                customer_id=customers[c.id].id,
                city=c.city,
                pincode=c.pincode,
                address_enc=encrypt(_address(c.id, c.city, c.pincode)),
                address_index=address_index(_address(c.id, c.city, c.pincode)),
            )
        )
    for o in data.orders:
        order = Order(
            external_id=o.id,
            customer_id=customers[o.customer_id].id,
            placed_at=o.placed_at,
            delivered_at=o.delivered_at,
            status=o.status,
            payment_method=o.payment_method,
            coupon_minor=o.coupon_minor,
            total_minor=o.total_minor,
        )
        order.items = [
            OrderItem(
                external_id=i.id,
                sku=i.sku,
                qty=i.qty,
                unit_price_minor=i.unit_price_minor,
                discount_alloc_minor=i.discount_alloc_minor,
                final_sale=i.final_sale,
            )
            for i in o.items
        ]
        session.add(order)
    if staff_password:
        for role in sorted(STAFF_ROLES):
            session.add(
                StaffUser(
                    email=f"{role.value}@saferetuns.dev",
                    password_hash=hash_password(staff_password),
                    role=role.value,
                    authority_limit_minor=AUTHORITY_LIMITS.get(role.value, 0),
                )
            )
    session.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customers", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--staff-password", help="creates <role>@saferetuns.dev demo users")
    args = parser.parse_args()
    if args.staff_password and len(args.staff_password) < 12:
        parser.error("--staff-password must be at least 12 characters")
    with Session(get_engine()) as session:
        loaded = load(
            session, generate(seed=args.seed, customers=args.customers), args.staff_password
        )
    print("seed loaded" if loaded else "seed already present; nothing changed")


if __name__ == "__main__":
    main()
