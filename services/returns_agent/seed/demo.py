"""Demo data for the walkthrough: two customers whose orders cover every story beat.

Run after `make seed-db` (products and staff must exist):
  uv run python -m returns_agent.seed.demo --staff-password '<min 12 chars>'

Priya (+91 90000 00001) — an instant refund, a refund without return, a final-sale
rejection, a damaged item that needs a photo, an approval, and a two-person approval.
Rahul (+91 90000 00002) — a heavy returner whose new return goes to fraud review.
"""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.db.models import (
    Address,
    Customer,
    Order,
    OrderItem,
    Product,
    ReturnCase,
    StaffUser,
)
from returns_agent.db.session import get_engine
from returns_agent.security.pii import address_index, blind_index, encrypt
from returns_agent.security.tokens import hash_password

PRIYA = "+91 90000 00001"
RAHUL = "+91 90000 00002"
# (order ref, sku, days since delivery, payment, final sale, what it demonstrates)
PRIYA_ORDERS = [
    ("DEMO-1001", "KUR-M", 3, "upi", False, "size problem: instant refund or exchange"),
    ("DEMO-1002", "SRM-30ml", 2, "upi", False, "cheap cosmetic: refund without return"),
    ("DEMO-1003", "KUR-L", 4, "card", True, "final sale: declined with the clause"),
    ("DEMO-1004", "EBD-white", 1, "upi", False, "damaged: photo, then replacement"),
    ("DEMO-1005", "SWT-black", 5, "cod", False, "₹5,999: needs one approver"),
    ("DEMO-1006", "PHN-blue", 2, "card", False, "₹42,999: needs two approvers"),
]


def _customer(
    session: Session, ref: str, name: str, phone: str, email: str, age_days: int
) -> Customer:
    customer = Customer(
        external_id=ref,
        name_enc=encrypt(name),
        phone_enc=encrypt(phone),
        phone_index=blind_index(phone),
        email_enc=encrypt(email),
        email_index=blind_index(email),
        tier="silver",
        account_age_days=age_days,
    )
    session.add(customer)
    session.flush()
    address = f"Flat 12, Madhapur, Hyderabad 500081 ({ref})"
    session.add(
        Address(
            customer_id=customer.id,
            city="Hyderabad",
            pincode="500081",
            address_enc=encrypt(address),
            address_index=address_index(address),
        )
    )
    return customer


def _order(
    session: Session,
    customer: Customer,
    ref: str,
    sku: str,
    days_ago: int,
    payment: str,
    final_sale: bool,
    now: datetime,
) -> Order:
    product = session.scalars(select(Product).where(Product.sku == sku)).one()
    order = Order(
        external_id=ref,
        customer_id=customer.id,
        placed_at=now - timedelta(days=days_ago + 3),
        delivered_at=now - timedelta(days=days_ago),
        status="delivered",
        payment_method=payment,
        coupon_minor=0,
        total_minor=product.price_minor,
    )
    order.items = [
        OrderItem(
            external_id=f"{ref}-1",
            sku=sku,
            qty=1,
            unit_price_minor=product.price_minor,
            discount_alloc_minor=0,
            final_sale=final_sale,
        )
    ]
    session.add(order)
    return order


def load_demo(session: Session, staff_password: str | None = None) -> bool:
    """Returns False (and changes nothing) if the demo customers already exist."""
    if session.scalar(select(Customer.id).where(Customer.external_id == "DEMO-PRIYA")):
        return False
    now = datetime.now(UTC)
    priya = _customer(session, "DEMO-PRIYA", "Priya Reddy", PRIYA, "priya.demo@example.com", 640)
    for ref, sku, days, payment, final_sale, _ in PRIYA_ORDERS:
        _order(session, priya, ref, sku, days, payment, final_sale, now)

    rahul = _customer(session, "DEMO-RAHUL", "Rahul Verma", RAHUL, "rahul.demo@example.com", 200)
    _order(session, rahul, "DEMO-2001", "JNS-32", 3, "upi", False, now)
    for i in range(6):  # six earlier returns in the last 90 days
        past = _order(session, rahul, f"DEMO-29{i:02d}", "TSH-M", 40, "upi", False, now)
        session.flush()
        session.add(
            ReturnCase(
                customer_id=rahul.id,
                order_id=past.id,
                graph_version="returns-v1",
                status="closed",
                current_node="CLOSE",
                closed_at=now - timedelta(days=30),
            )
        )
    if staff_password:  # a second approver, for the two-person rule
        email = "approver2@saferetuns.dev"
        if session.scalar(select(StaffUser.id).where(StaffUser.email == email)) is None:
            session.add(
                StaffUser(
                    email=email,
                    password_hash=hash_password(staff_password),
                    role="approver",
                    authority_limit_minor=2_500_000,
                )
            )
    session.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staff-password", help="password for approver2@saferetuns.dev")
    args = parser.parse_args()
    with Session(get_engine()) as session:
        created = load_demo(session, args.staff_password)
    print("demo data loaded" if created else "demo data already present")
    print(f"customers: Priya {PRIYA}, Rahul {RAHUL}")


if __name__ == "__main__":
    main()
