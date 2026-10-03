import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from returns_agent.agent.cases import CaseNotFound, CaseService, CaseView
from returns_agent.api.deps import Adapters, get_adapters, get_cases, require
from returns_agent.audit import log as audit
from returns_agent.db.models import Order, ReturnCase, StaffUser
from returns_agent.db.session import get_session
from returns_agent.graph.events import InvalidEvent
from returns_agent.graph.runner import CaseClosed, StaleEvent
from returns_agent.security.otp import InvalidOtp, OtpRateLimited, request_otp, verify_otp
from returns_agent.security.tokens import (
    STAFF_ROLES,
    InvalidToken,
    Principal,
    Role,
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)

DB = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/api/v1")


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _tokens(principal: Principal) -> TokenPair:
    return TokenPair(
        access_token=issue_token(principal, "access"),
        refresh_token=issue_token(principal, "refresh"),
    )


# --- Auth (public) ---------------------------------------------------------------------


class OtpRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=20)


class OtpVerify(OtpRequest):
    code: str = Field(pattern=r"^\d{6}$")


class StaffLogin(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/auth/otp/request", status_code=status.HTTP_202_ACCEPTED)
def otp_request(
    body: OtpRequest, db: DB, adapters: Annotated[Adapters, Depends(get_adapters)]
) -> dict[str, str]:
    try:
        request_otp(db, body.phone, adapters.notification)
    except OtpRateLimited as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many codes requested") from exc
    db.commit()
    return {"status": "sent"}


@router.post("/auth/otp/verify")
def otp_verify(body: OtpVerify, db: DB) -> TokenPair:
    try:
        customer_id = verify_otp(db, body.phone, body.code)
    except InvalidOtp as exc:
        db.commit()  # persist the failed-attempt count
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid code") from exc
    audit.append(db, actor_type="customer", actor_id=str(customer_id), action="login.otp")
    db.commit()
    return _tokens(Principal(str(customer_id), Role.CUSTOMER))


@router.post("/auth/staff/login")
def staff_login(body: StaffLogin, db: DB) -> TokenPair:
    user = db.scalar(select(StaffUser).where(StaffUser.email == body.email.lower()))
    if (
        user is None
        or user.status != "active"
        or not verify_password(user.password_hash, body.password)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    audit.append(db, actor_type="staff", actor_id=str(user.id), action="login.staff")
    db.commit()
    return _tokens(Principal(str(user.id), Role(user.role)))


@router.post("/auth/refresh")
def refresh(body: RefreshRequest) -> TokenPair:
    try:
        principal = decode_token(body.refresh_token, "refresh")
    except InvalidToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token") from exc
    return _tokens(principal)


# --- Customer ----------------------------------------------------------------------------


class OrderSummary(BaseModel):
    order_id: str
    status: str
    total_minor: int
    item_count: int


@router.get("/me/orders")
def my_orders(
    db: DB, principal: Annotated[Principal, require(Role.CUSTOMER)]
) -> list[OrderSummary]:
    orders = db.scalars(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.customer_id == uuid.UUID(principal.subject))  # own orders only
        .order_by(Order.placed_at.desc())
    ).all()
    return [
        OrderSummary(
            order_id=o.external_id,
            status=o.status,
            total_minor=o.total_minor,
            item_count=len(o.items),
        )
        for o in orders
    ]


# --- Staff -------------------------------------------------------------------------------


class CaseSummary(BaseModel):
    case_id: str
    status: str
    current_node: str
    route: str | None


@router.get("/console/cases")
def console_cases(
    db: DB, _: Annotated[Principal, require(Role.AGENT, Role.APPROVER, Role.ADMIN)]
) -> list[CaseSummary]:
    cases = db.scalars(select(ReturnCase).order_by(ReturnCase.created_at.desc()).limit(100)).all()
    return [
        CaseSummary(case_id=str(c.id), status=c.status, current_node=c.current_node, route=c.route)
        for c in cases
    ]


class StaffUserOut(BaseModel):
    id: str
    email: str
    role: Role
    authority_limit_minor: int
    status: str


class StaffUserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    role: Role
    authority_limit_minor: int = Field(default=0, ge=0)


def _out(u: StaffUser) -> StaffUserOut:
    return StaffUserOut(
        id=str(u.id),
        email=u.email,
        role=Role(u.role),
        authority_limit_minor=u.authority_limit_minor,
        status=u.status,
    )


@router.get("/admin/users")
def list_staff(db: DB, _: Annotated[Principal, require(Role.ADMIN)]) -> list[StaffUserOut]:
    return [_out(u) for u in db.scalars(select(StaffUser).order_by(StaffUser.email)).all()]


@router.post("/admin/users", status_code=status.HTTP_201_CREATED)
def create_staff(
    body: StaffUserIn, db: DB, admin: Annotated[Principal, require(Role.ADMIN)]
) -> StaffUserOut:
    if body.role not in STAFF_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a staff role")
    user = StaffUser(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        role=body.role.value,
        authority_limit_minor=body.authority_limit_minor,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already exists") from exc
    audit.append(
        db,
        actor_type="staff",
        actor_id=admin.subject,
        action="staff.create",
        payload={"user_id": str(user.id), "role": user.role},
    )
    db.commit()
    return _out(user)


@router.get("/analytics/summary")
def analytics_summary(
    db: DB, _: Annotated[Principal, require(Role.ANALYST, Role.ADMIN)]
) -> dict[str, int]:
    return {
        "orders": db.scalar(select(func.count()).select_from(Order)) or 0,
        "cases": db.scalar(select(func.count()).select_from(ReturnCase)) or 0,
    }


# --- Customer cases ------------------------------------------------------------------------
# The role guard is the first dependency of every endpoint, so unauthorised callers get 403
# before anything else is resolved.

Cases = Annotated[CaseService, Depends(get_cases)]
CustomerOnly = Annotated[Principal, require(Role.CUSTOMER)]


class OpenCase(BaseModel):
    order_id: str
    item_id: str
    qty: int = Field(default=1, ge=1)
    message: str = Field(min_length=1, max_length=4000)


class CustomerText(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Confirm(BaseModel):
    accept: bool
    option: str | None = None


def _case_call(fn: Callable[[], CaseView]) -> CaseView:
    try:
        return fn()
    except CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (StaleEvent, CaseClosed) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InvalidEvent as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/cases", status_code=status.HTTP_201_CREATED)
def open_case(principal: CustomerOnly, body: OpenCase, db: DB, cases: Cases) -> CaseView:
    customer = uuid.UUID(principal.subject)
    return _case_call(
        lambda: cases.open(db, customer, body.order_id, body.item_id, body.qty, body.message)
    )


@router.post("/cases/{case_id}/messages")
def case_message(
    principal: CustomerOnly, case_id: uuid.UUID, body: CustomerText, db: DB, cases: Cases
) -> CaseView:
    customer = uuid.UUID(principal.subject)
    return _case_call(lambda: cases.message(db, customer, case_id, body.text))


@router.post("/cases/{case_id}/confirm")
def case_confirm(
    principal: CustomerOnly, case_id: uuid.UUID, body: Confirm, db: DB, cases: Cases
) -> CaseView:
    customer = uuid.UUID(principal.subject)
    return _case_call(lambda: cases.confirm(db, customer, case_id, body.accept, body.option))


@router.get("/cases/{case_id}/messages")
def case_history(
    principal: CustomerOnly, case_id: uuid.UUID, db: DB, cases: Cases
) -> list[dict[str, str]]:
    try:
        return cases.history(db, uuid.UUID(principal.subject), case_id)
    except CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
