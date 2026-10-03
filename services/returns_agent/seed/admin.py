"""Create the first admin (the store owner) for a real setup. No default passwords.

  uv run python -m returns_agent.seed.admin --email owner@yourstore.in
The password is asked for interactively (or read from ADMIN_PASSWORD for automation).
Everyone else is invited from the admin studio's Users tab.
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.audit import log as audit
from returns_agent.db.models import StaffUser
from returns_agent.db.session import get_engine
from returns_agent.security.tokens import hash_password

ADMIN_LIMIT_MINOR = 2_500_000
MIN_PASSWORD = 12


def create_admin(session: Session, email: str, password: str) -> StaffUser:
    email = email.strip().lower()
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if session.scalar(select(StaffUser.id).where(StaffUser.email == email)):
        raise ValueError(f"{email} already exists")
    admin = StaffUser(
        email=email,
        password_hash=hash_password(password),
        role="admin",
        authority_limit_minor=ADMIN_LIMIT_MINOR,
    )
    session.add(admin)
    session.flush()
    audit.append(
        session, actor_type="system", action="staff.admin_created", payload={"email": email}
    )
    session.commit()
    return admin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    if not os.environ.get("ADMIN_PASSWORD") and getpass.getpass("Repeat password: ") != password:
        sys.exit("passwords do not match")
    with Session(get_engine()) as session:
        try:
            create_admin(session, args.email, password)
        except ValueError as exc:
            sys.exit(str(exc))
    print(f"admin {args.email} created; sign in at /console/login and invite your team")


if __name__ == "__main__":
    main()
