"""Runs at API start-up (hosting without a shell, e.g. Render's free tier). Idempotent:

- loads the product catalogue if products are missing;
- creates the first admin when BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD are set;
- gives the four standard orders to every account that has no orders yet.
"""

import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.db.models import StaffUser
from returns_agent.db.session import get_engine
from returns_agent.seed import customer_orders
from returns_agent.seed.admin import create_admin

logger = logging.getLogger(__name__)


def ensure_admin(session: Session, email: str, password: str) -> bool:
    if session.scalar(select(StaffUser.id).where(StaffUser.email == email.strip().lower())):
        return False
    create_admin(session, email, password)
    return True


def run() -> None:
    with Session(get_engine()) as session:
        try:
            added = customer_orders.ensure_catalog(session)
            session.commit()
            if added:
                logger.info("bootstrap: %d products added", added)
            email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
            password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
            if email and password and ensure_admin(session, email, password):
                logger.info("bootstrap: admin %s created", email)
            accounts = customer_orders.backfill(session)
            if accounts:
                logger.info("bootstrap: orders added to %d accounts", accounts)
        except Exception as exc:  # never stop the API from starting because of seed data
            session.rollback()
            logger.exception("bootstrap failed: %s: %s", type(exc).__name__, exc)
