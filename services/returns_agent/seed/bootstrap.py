"""Startup bootstrap for hosted demos (no shell on free hosting). Everything is idempotent and
off unless its environment variable is set:

  BOOTSTRAP_ADMIN_EMAIL + BOOTSTRAP_ADMIN_PASSWORD  create the first admin
  DEMO_PHONES=+919391182021,...                      five delivered demo orders per number
  DEMO_ESCALATION=true                               a demo customer whose return is escalated
                                                     to a person (shows in the support console)
The product catalogue is loaded whenever any of these is set.
"""

import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.agent.cases import CaseService
from returns_agent.db.models import Customer, Order, OrderItem, Product, ReturnCase, StaffUser
from returns_agent.db.session import get_engine
from returns_agent.security.pii import blind_index, encrypt
from returns_agent.seed.admin import create_admin
from returns_agent.seed.demo import RAHUL, load_demo
from returns_agent.seed.generator import generate

logger = logging.getLogger(__name__)

# (sku, qty, days since delivery, payment): a varied order history for the demo account
DEMO_ORDERS: list[tuple[list[tuple[str, int]], int, str]] = [
    ([("SNK-9", 1)], 2, "upi"),
    ([("SWT-black", 1)], 4, "card"),
    ([("EBD-white", 1)], 1, "upi"),
    ([("KUR-M", 1), ("TSH-L", 2)], 6, "cod"),
    ([("PHN-blue", 1)], 3, "card"),
]


def ensure_catalog(session: Session) -> int:
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
    session.commit()
    return len(new)


def ensure_admin(session: Session, email: str, password: str) -> bool:
    if session.scalar(select(StaffUser.id).where(StaffUser.email == email.strip().lower())):
        return False
    create_admin(session, email, password)
    return True


def _e164(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    return f"+91{digits}" if len(digits) == 10 else f"+{digits}"


def ensure_demo_orders(session: Session, phone: str) -> int:
    """Creates the customer if they have not signed up yet (profile is completed on first
    sign-in), then the demo orders, once."""
    phone = _e164(phone)
    tag = phone[-4:]
    customer = session.scalar(select(Customer).where(Customer.phone_index == blind_index(phone)))
    if customer is None:
        customer = Customer(
            external_id=f"DEMO-{tag}",
            name_enc=encrypt(""),
            phone_enc=encrypt(phone),
            phone_index=blind_index(phone),
            email_enc=encrypt(""),
            email_index=None,
            account_age_days=420,
        )
        session.add(customer)
        session.flush()
    prefix = f"VAP-{tag}-"
    if session.scalar(select(Order.id).where(Order.external_id.startswith(prefix))):
        return 0
    prices = {p.sku: p.price_minor for p in session.scalars(select(Product))}
    now = datetime.now(UTC)
    for n, (lines, days, payment) in enumerate(DEMO_ORDERS, start=1):
        ref = f"{prefix}{n:02d}"
        order = Order(
            external_id=ref,
            customer_id=customer.id,
            placed_at=now - timedelta(days=days + 3),
            delivered_at=now - timedelta(days=days),
            status="delivered",
            payment_method=payment,
            coupon_minor=0,
            total_minor=sum(prices[s] * q for s, q in lines),
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
    session.commit()
    return len(DEMO_ORDERS)


def ensure_escalated_case(session: Session, cases: CaseService) -> bool:
    """Rahul (a frequent returner) asks to return jeans: the risk score routes it to a person."""
    load_demo(session)
    rahul = session.scalar(select(Customer).where(Customer.phone_index == blind_index(RAHUL)))
    order = session.scalar(select(Order).where(Order.external_id == "DEMO-2001"))
    if rahul is None or order is None:
        return False
    if session.scalar(select(ReturnCase.id).where(ReturnCase.order_id == order.id)):
        return False
    item = session.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).first()
    assert item is not None
    cases.open(
        session,
        rahul.id,
        order.external_id,
        item.external_id,
        1,
        "These jeans don't fit at the waist. I want my money back today.",
        {"reason_category": "size_fit", "desired_resolution": "refund"},
    )
    return True


def run(cases: CaseService) -> None:
    admin_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
    admin_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    phones = [p for p in os.environ.get("DEMO_PHONES", "").split(",") if p.strip()]
    escalation = os.environ.get("DEMO_ESCALATION", "").lower() in ("1", "true", "yes")
    if not (admin_email or phones or escalation):
        return
    with Session(get_engine()) as session:
        try:
            added = ensure_catalog(session)
            if added:
                logger.info("bootstrap: %d products added", added)
            if (
                admin_email
                and admin_password
                and ensure_admin(session, admin_email, admin_password)
            ):
                logger.info("bootstrap: admin %s created", admin_email)
            for phone in phones:
                if ensure_demo_orders(session, phone):
                    logger.info("bootstrap: demo orders created for ...%s", _e164(phone)[-4:])
            if escalation and ensure_escalated_case(session, cases):
                logger.info("bootstrap: escalated demo case created")
        except Exception:  # never stop the API from starting because of demo data
            session.rollback()
            logger.exception("bootstrap failed")
