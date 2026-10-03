import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from returns_agent.agent.cases import CaseNotFound, CaseService, CaseView, ReviewNotAvailable
from returns_agent.analytics import summary as analytics
from returns_agent.api.deps import Adapters, get_adapters, get_cases, require
from returns_agent.audit import log as audit
from returns_agent.config import config_dir, get_settings
from returns_agent.db.models import (
    Address,
    Approval,
    Customer,
    Evidence,
    Order,
    OrderItem,
    Product,
    QueueItem,
    ReturnCase,
    ReturnItem,
    StaffUser,
)
from returns_agent.db.session import get_session
from returns_agent.decision.config import load_decision_config
from returns_agent.evidence.intake import MAX_BYTES, MAX_FILES, EvidenceRejected
from returns_agent.evidence.service import Upload
from returns_agent.execution.webhooks import (
    CarrierWebhook,
    PaymentWebhook,
    WebhookAuthError,
    handle_carrier,
    handle_payment,
    verify_signature,
)
from returns_agent.graph.events import InvalidEvent
from returns_agent.graph.runner import CaseClosed, Event, StaleEvent
from returns_agent.hitl import approvals, goodwill, handoff, queues
from returns_agent.lifecycle.timeline import build_timeline
from returns_agent.policy.schema import load_policies
from returns_agent.security.firebase import InvalidFirebaseToken, verify_id_token
from returns_agent.security.otp import InvalidOtp, OtpRateLimited, request_otp, verify_otp
from returns_agent.security.pii import address_index, blind_index, decrypt, encrypt
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
        code = request_otp(db, body.phone, adapters.notification)
    except OtpRateLimited as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many codes requested") from exc
    db.commit()
    if get_settings().dev_otp_echo and code:  # development demo only (no SMS gateway)
        return {"status": "sent", "dev_code": code}
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


class FirebaseLogin(BaseModel):
    id_token: str = Field(min_length=20, max_length=4096)


class CustomerSession(TokenPair):
    profile_complete: bool


def _profile_complete(db: Session, customer: Customer) -> bool:
    has_address = db.scalar(select(Address.id).where(Address.customer_id == customer.id))
    return bool(decrypt(customer.name_enc)) and has_address is not None


@router.post("/auth/firebase")
def firebase_login(body: FirebaseLogin, db: DB) -> CustomerSession:
    """Sign in or sign up with a phone number verified by Firebase Phone Auth."""
    try:
        identity = verify_id_token(body.id_token)
    except InvalidFirebaseToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    index = blind_index(identity.phone)
    customer = db.scalar(select(Customer).where(Customer.phone_index == index))
    action = "login.firebase"
    if customer is None:  # first sign-in with this number: create the account
        customer = Customer(
            external_id=f"CUST-{uuid.uuid4().hex[:12].upper()}",
            name_enc=encrypt(""),
            phone_enc=encrypt(identity.phone),
            phone_index=index,
            email_enc=encrypt(""),
            email_index=None,
            account_age_days=0,
        )
        db.add(customer)
        db.flush()
        action = "signup.firebase"
    audit.append(db, actor_type="customer", actor_id=str(customer.id), action=action)
    complete = _profile_complete(db, customer)
    db.commit()
    tokens = _tokens(Principal(str(customer.id), Role.CUSTOMER))
    return CustomerSession(**tokens.model_dump(), profile_complete=complete)


class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr | None = None
    address: str = Field(min_length=5, max_length=300)
    city: str = Field(min_length=2, max_length=64)
    pincode: str = Field(pattern=r"^[1-9][0-9]{5}$")


class ProfileOut(BaseModel):
    name: str
    phone: str
    email: str | None
    address: str | None
    city: str | None
    pincode: str | None
    profile_complete: bool


def _profile(db: Session, customer: Customer) -> ProfileOut:
    address = db.scalars(select(Address).where(Address.customer_id == customer.id)).first()
    return ProfileOut(
        name=decrypt(customer.name_enc),
        phone=decrypt(customer.phone_enc),
        email=decrypt(customer.email_enc) or None,
        address=decrypt(address.address_enc) if address else None,
        city=address.city if address else None,
        pincode=address.pincode if address else None,
        profile_complete=_profile_complete(db, customer),
    )


@router.get("/me/profile")
def get_profile(db: DB, principal: Annotated[Principal, require(Role.CUSTOMER)]) -> ProfileOut:
    customer = db.get(Customer, uuid.UUID(principal.subject))
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    return _profile(db, customer)


@router.put("/me/profile")
def put_profile(
    body: Profile, db: DB, principal: Annotated[Principal, require(Role.CUSTOMER)]
) -> ProfileOut:
    customer = db.get(Customer, uuid.UUID(principal.subject))
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    customer.name_enc = encrypt(body.name.strip())
    if body.email:
        customer.email_enc = encrypt(body.email.lower())
        customer.email_index = blind_index(body.email.lower())
    address = db.scalars(select(Address).where(Address.customer_id == customer.id)).first()
    if address is None:
        address = Address(customer_id=customer.id, city="", pincode="", address_enc="")
        db.add(address)
    address.city, address.pincode = body.city.strip(), body.pincode
    full = f"{body.address.strip()}, {body.city.strip()} {body.pincode}"
    address.address_enc, address.address_index = encrypt(full), address_index(full)
    audit.append(db, actor_type="customer", actor_id=str(customer.id), action="profile.updated")
    db.commit()
    return _profile(db, customer)


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


class OrderItemOut(BaseModel):
    item_id: str
    sku: str
    title: str
    qty: int
    unit_price_minor: int
    final_sale: bool


class OrderSummary(BaseModel):
    order_id: str
    status: str
    total_minor: int
    item_count: int
    payment_method: str
    placed_at: datetime
    delivered_at: datetime | None
    items: list[OrderItemOut]


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
    titles = dict(db.execute(select(Product.sku, Product.title)).tuples().all())
    return [
        OrderSummary(
            order_id=o.external_id,
            status=o.status,
            total_minor=o.total_minor,
            item_count=len(o.items),
            payment_method=o.payment_method,
            placed_at=o.placed_at,
            delivered_at=o.delivered_at,
            items=[
                OrderItemOut(
                    item_id=i.external_id,
                    sku=i.sku,
                    title=titles.get(i.sku, i.sku),
                    qty=i.qty,
                    unit_price_minor=i.unit_price_minor,
                    final_sale=i.final_sale,
                )
                for i in o.items
            ],
        )
        for o in orders
    ]


# --- Staff -------------------------------------------------------------------------------


class CaseSummary(BaseModel):
    case_id: str
    status: str
    current_node: str
    route: str | None
    priority: int = 0
    order_id: str | None = None
    created_at: datetime | None = None
    sla_due_at: datetime | None = None


@router.get("/console/cases")
def console_cases(
    db: DB,
    _: Annotated[Principal, require(Role.AGENT, Role.APPROVER, Role.ADMIN)],
    status_: Annotated[str | None, Query(alias="status")] = None,
    route: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CaseSummary]:
    query = select(ReturnCase, Order.external_id).join(Order, Order.id == ReturnCase.order_id)
    if status_:
        query = query.where(ReturnCase.status == status_)
    if route:
        query = query.where(ReturnCase.route == route)
    rows = db.execute(
        query.order_by(ReturnCase.priority.desc(), ReturnCase.created_at.desc()).limit(limit)
    ).all()
    return [
        CaseSummary(
            case_id=str(c.id),
            status=c.status,
            current_node=c.current_node,
            route=c.route,
            priority=c.priority,
            order_id=order_ref,
            created_at=c.created_at,
            sla_due_at=c.sla_due_at,
        )
        for c, order_ref in rows
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
    db: DB,
    _: Annotated[Principal, require(Role.ANALYST, Role.ADMIN)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    return analytics.summary(db, days)


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
    # Optional selections from the UI; the agent extracts them from the message otherwise.
    reason_category: (
        Literal[
            "size_fit",
            "damaged",
            "defective",
            "wrong_item",
            "not_as_described",
            "changed_mind",
            "other",
        ]
        | None
    ) = None
    desired_resolution: Literal["refund", "exchange", "replacement", "store_credit"] | None = None
    exchange_sku: str | None = Field(default=None, max_length=64)  # size/colour picked for exchange
    is_gift: bool | None = None


class CustomerText(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Confirm(BaseModel):
    accept: bool
    option: str | None = None
    refund_method: Literal["source", "bank_transfer", "upi", "store_credit"] | None = None
    exchange_sku: str | None = Field(default=None, max_length=64)


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
        lambda: cases.open(
            db,
            customer,
            body.order_id,
            body.item_id,
            body.qty,
            body.message,
            {
                "reason_category": body.reason_category,
                "desired_resolution": body.desired_resolution,
                "exchange_sku": body.exchange_sku,
                "is_gift": body.is_gift,
            },
        )
    )


class MyCase(BaseModel):
    case_id: str
    status: str
    current_node: str
    order_id: str
    sku: str | None
    created_at: datetime


@router.get("/cases")
def my_cases(principal: CustomerOnly, db: DB) -> list[MyCase]:
    rows = db.execute(
        select(ReturnCase, Order.external_id, OrderItem.sku)
        .join(Order, Order.id == ReturnCase.order_id)
        .outerjoin(ReturnItem, ReturnItem.case_id == ReturnCase.id)
        .outerjoin(OrderItem, OrderItem.id == ReturnItem.order_item_id)
        .where(ReturnCase.customer_id == uuid.UUID(principal.subject))  # own cases only
        .order_by(ReturnCase.created_at.desc())
    ).all()
    return [
        MyCase(
            case_id=str(c.id),
            status=c.status,
            current_node=c.current_node,
            order_id=order_ref,
            sku=sku,
            created_at=c.created_at,
        )
        for c, order_ref, sku in rows
    ]


@router.get("/cases/{case_id}")
def case_view(principal: CustomerOnly, case_id: uuid.UUID, db: DB, cases: Cases) -> CaseView:
    return _case_call(lambda: cases.view(db, uuid.UUID(principal.subject), case_id))


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
    return _case_call(
        lambda: cases.confirm(
            db, customer, case_id, body.accept, body.option, body.refund_method, body.exchange_sku
        )
    )


@router.post("/cases/{case_id}/evidence")
def upload_evidence(
    principal: CustomerOnly,
    case_id: uuid.UUID,
    db: DB,
    cases: Cases,
    files: Annotated[list[UploadFile], File(description="1-5 JPEG/PNG/WebP photos, 10 MB each")],
) -> CaseView:
    if len(files) > MAX_FILES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Upload at most {MAX_FILES} photos."
        )
    # Read one byte past the limit so oversized files are detected without reading them whole.
    uploads = [Upload(f.filename or "photo", f.file.read(MAX_BYTES + 1)) for f in files]
    try:
        return _case_call(lambda: cases.upload(db, uuid.UUID(principal.subject), case_id, uploads))
    except EvidenceRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/cases/{case_id}/messages")
def case_history(
    principal: CustomerOnly, case_id: uuid.UUID, db: DB, cases: Cases
) -> list[dict[str, str]]:
    try:
        return cases.history(db, uuid.UUID(principal.subject), case_id)
    except CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/cases/{case_id}/timeline")
def customer_timeline(principal: CustomerOnly, case_id: uuid.UUID, db: DB) -> list[dict[str, Any]]:
    case = db.get(ReturnCase, case_id)
    if case is None or case.customer_id != uuid.UUID(principal.subject):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return build_timeline(db, case_id, "customer")


# --- Staff case operations --------------------------------------------------------------------

StaffOps = Annotated[Principal, require(Role.AGENT, Role.APPROVER, Role.ADMIN)]
QcStaff = Annotated[Principal, require(Role.QC_OPERATOR, Role.ADMIN)]


class QcBody(BaseModel):
    passed: bool
    grade: Literal["A", "B", "C", "D"] | None = None


@router.get("/console/cases/{case_id}/timeline")
def staff_timeline(principal: StaffOps, case_id: uuid.UUID, db: DB) -> list[dict[str, Any]]:
    if db.get(ReturnCase, case_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return build_timeline(db, case_id, "staff")


@router.post("/console/cases/{case_id}/qc")
def record_qc(
    principal: QcStaff, case_id: uuid.UUID, body: QcBody, db: DB, cases: Cases
) -> dict[str, Any]:
    if db.get(ReturnCase, case_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    payload: dict[str, Any] = {"passed": body.passed}
    if body.grade:
        payload["grade"] = body.grade
    try:
        result = cases.runner.dispatch(
            case_id, Event("qc_result", payload, "staff", principal.subject)
        )
    except (StaleEvent, CaseClosed) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"case_id": str(case_id), "current_node": result.current_node, "status": result.status}


# --- Provider webhooks (HMAC-authenticated, no bearer token) ------------------------------------


async def _verified_body(request: Request) -> bytes:
    body = await request.body()
    settings = get_settings()
    try:
        verify_signature(
            settings.webhook_secret,
            request.headers.get("x-timestamp"),
            request.headers.get("x-signature"),
            body,
            settings.webhook_tolerance_s,
        )
    except WebhookAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return body


@router.post("/webhooks/carrier")
async def carrier_webhook(request: Request, db: DB, cases: Cases) -> dict[str, str]:
    body = await _verified_body(request)
    hook = CarrierWebhook.model_validate_json(body)
    return {"result": handle_carrier(db, cases.runner, hook)}


@router.post("/webhooks/payment")
async def payment_webhook(request: Request, db: DB) -> dict[str, str]:
    body = await _verified_body(request)
    return {"result": handle_payment(db, PaymentWebhook.model_validate_json(body))}


# --- Human in the loop: queues, handoff, approvals, resolutions, goodwill, reviews -------------

Approvers = Annotated[Principal, require(Role.APPROVER, Role.ADMIN)]
RESOLUTION_REASONS = frozenset(
    {"handled_offline", "policy_upheld", "customer_withdrew", "fraud_confirmed", "duplicate_case"}
)
REVIEW_REASONS = frozenset({"policy_upheld", "policy_misapplied", "goodwill_exception"})


def _staff(db: Session, principal: Principal) -> StaffUser:
    try:
        staff = db.get(StaffUser, uuid.UUID(principal.subject))
    except ValueError:
        staff = None
    if staff is None or staff.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "staff account not found or inactive")
    return staff


def _case_or_404(db: Session, case_id: uuid.UUID) -> ReturnCase:
    case = db.get(ReturnCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return case


def _hitl_error(exc: approvals.ApprovalError) -> HTTPException:
    code = {
        approvals.NotAllowed: status.HTTP_403_FORBIDDEN,
        approvals.Conflict: status.HTTP_409_CONFLICT,
    }.get(type(exc), status.HTTP_422_UNPROCESSABLE_CONTENT)
    return HTTPException(code, str(exc))


def _staff_audit(
    db: Session, staff: StaffUser, case_id: uuid.UUID, action: str, **payload: Any
) -> None:
    audit.append(
        db,
        actor_type="staff",
        actor_id=str(staff.id),
        case_id=case_id,
        action=action,
        payload={"role": staff.role, **payload},
    )


class QueueItemOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    queue: str
    status: str
    priority: int
    reason: str
    assignee_id: uuid.UUID | None
    due_at: datetime
    escalated: bool
    outcome: str | None

    @classmethod
    def of(cls, item: QueueItem) -> "QueueItemOut":
        return cls(
            id=item.id,
            case_id=item.case_id,
            queue=item.queue,
            status=item.status,
            priority=item.priority,
            reason=item.reason,
            assignee_id=item.assignee_id,
            due_at=item.due_at,
            escalated=item.escalated_at is not None,
            outcome=item.outcome,
        )


@router.get("/console/queues/{queue}")
def queue_items(principal: StaffOps, queue: str, db: DB) -> list[QueueItemOut]:
    if queue not in queues.QUEUES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown queue; use one of {queues.QUEUES}")
    return [QueueItemOut.of(i) for i in queues.list_open(db, queue)]


@router.post("/console/queue-items/{item_id}/claim")
def claim_item(principal: StaffOps, item_id: uuid.UUID, db: DB) -> QueueItemOut:
    staff = _staff(db, principal)
    item = db.get(QueueItem, item_id, with_for_update=True)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "queue item not found")
    if item.status == "done" or (item.assignee_id not in (None, staff.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "item is closed or assigned to someone else")
    item.status, item.assignee_id = "assigned", staff.id
    _staff_audit(db, staff, item.case_id, "queue.claimed", queue=item.queue)
    db.commit()
    return QueueItemOut.of(item)


@router.get("/console/cases/{case_id}/handoff")
def handoff_packet(principal: StaffOps, case_id: uuid.UUID, db: DB, cases: Cases) -> dict[str, Any]:
    _case_or_404(db, case_id)
    return handoff.packet(db, case_id, cases.runner.facts(case_id))


class ApprovalBody(BaseModel):
    decision: Literal["approve", "modify", "reject"]
    reason_code: str = Field(min_length=1, max_length=64)
    option: str | None = None
    note: str = Field(default="", max_length=2000)


@router.post("/console/cases/{case_id}/approval")
def decide_approval(
    principal: Approvers, case_id: uuid.UUID, body: ApprovalBody, db: DB, cases: Cases
) -> dict[str, Any]:
    case = _case_or_404(db, case_id)
    if case.status == "closed" or case.current_node != "HUMAN_APPROVAL":
        raise HTTPException(status.HTTP_409_CONFLICT, "case is not waiting for approval")
    approval = db.scalars(
        select(Approval)
        .where(Approval.case_id == case_id, Approval.status.in_(approvals.OPEN))
        .with_for_update()
    ).first()
    if approval is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no open approval for this case")
    staff = _staff(db, principal)
    facts = cases.runner.facts(case_id)
    original = approval.requested_action
    now = datetime.now(UTC)
    try:
        token = approvals.decide(
            approval, staff, body.decision, body.reason_code, facts, now, body.option, body.note
        )
    except approvals.ApprovalError as exc:
        raise _hitl_error(exc) from exc
    _staff_audit(
        db,
        staff,
        case_id,
        "approval.signoff",
        approval=str(approval.id),
        decision=body.decision,
        reason_code=body.reason_code,
        option=approval.requested_action,
        amount_minor=approval.amount_minor,
        status=approval.status,
    )
    final = approval.status in ("approved", "rejected")
    if final:
        queues.close(db, case_id, "approval", approval.status, now)
    db.commit()
    result: dict[str, Any] = {
        "approval": approval.status,
        "signoffs": len(approval.signoffs),
        "required_approvals": approval.required_approvals,
    }
    if not final:
        return result
    decision = "reject" if approval.status == "rejected" else "approve"
    if decision == "approve" and approval.requested_action != original:
        decision = "modify"
    event = Event(
        "approval",
        {
            "decision": decision,
            "approver_id": str(staff.id),
            "token": token,
            "reason_code": body.reason_code,
            "option": approval.requested_action if decision == "modify" else None,
        },
        "staff",
        str(staff.id),
    )
    view = _case_call(lambda: cases.staff_event(db, case_id, event))
    return result | {"case": asdict(view)}


class ResolveBody(BaseModel):
    outcome: Literal["resolved_by_human", "rejected", "cancelled"]
    reason_code: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


@router.post("/console/cases/{case_id}/resolve")
def resolve_case(
    principal: StaffOps, case_id: uuid.UUID, body: ResolveBody, db: DB, cases: Cases
) -> CaseView:
    case = _case_or_404(db, case_id)
    if case.status == "closed" or case.current_node not in ("ESCALATE", "DISPUTE"):
        raise HTTPException(status.HTTP_409_CONFLICT, "case is not waiting for a person")
    if body.reason_code not in RESOLUTION_REASONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"reason code must be one of {sorted(RESOLUTION_REASONS)}",
        )
    staff = _staff(db, principal)
    now = datetime.now(UTC)
    if body.reason_code == "fraud_confirmed":  # feeds risk for later cases; never an automatic ban
        customer = db.get(Customer, case.customer_id)
        if customer is not None:
            customer.risk_profile = {**customer.risk_profile, "confirmed_fraud": True}
    for queue in ("escalation", "fraud_review", "dispute"):
        queues.close(db, case_id, queue, body.outcome, now)
    _staff_audit(
        db, staff, case_id, "case.resolved", outcome=body.outcome, reason_code=body.reason_code
    )
    db.commit()
    event = Event(
        "human_resolution",
        {"outcome": body.outcome, "staff_id": str(staff.id)},
        "staff",
        str(staff.id),
    )
    return _case_call(lambda: cases.staff_event(db, case_id, event))


class GoodwillBody(BaseModel):
    amount_minor: int = Field(gt=0)
    reason_code: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


@router.post("/console/cases/{case_id}/goodwill", status_code=status.HTTP_201_CREATED)
def grant_goodwill(
    principal: StaffOps, case_id: uuid.UUID, body: GoodwillBody, db: DB
) -> dict[str, Any]:
    case = _case_or_404(db, case_id)
    staff = _staff(db, principal)
    try:
        row = goodwill.grant(
            db, case, staff, body.amount_minor, body.reason_code, body.note, datetime.now(UTC)
        )
    except approvals.ApprovalError as exc:
        raise _hitl_error(exc) from exc
    _staff_audit(
        db,
        staff,
        case_id,
        "goodwill.granted",
        amount_minor=body.amount_minor,
        reason_code=body.reason_code,
    )
    db.commit()
    return {"id": str(row.id), "amount_minor": row.amount_minor, "status": row.status}


class ReviewOutcome(BaseModel):
    outcome: Literal["upheld", "overturned"]
    reason_code: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


@router.post("/console/queue-items/{item_id}/close")
def close_review(
    principal: StaffOps, item_id: uuid.UUID, body: ReviewOutcome, db: DB
) -> QueueItemOut:
    """Closes a customer-requested review. An overturned denial is honoured as a case-only
    exception (goodwill), never by changing policy."""
    staff = _staff(db, principal)
    item = db.get(QueueItem, item_id, with_for_update=True)
    if item is None or item.queue != "review":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "review item not found")
    if item.status == "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "review already closed")
    if body.reason_code not in REVIEW_REASONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"reason code must be one of {sorted(REVIEW_REASONS)}",
        )
    item.status, item.outcome, item.closed_at = "done", body.outcome, datetime.now(UTC)
    _staff_audit(
        db, staff, item.case_id, "review.closed", outcome=body.outcome, reason_code=body.reason_code
    )
    db.commit()
    return QueueItemOut.of(item)


class ReviewRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/cases/{case_id}/review", status_code=status.HTTP_202_ACCEPTED)
def request_review(
    principal: CustomerOnly, case_id: uuid.UUID, body: ReviewRequest, db: DB, cases: Cases
) -> dict[str, str]:
    try:
        cases.request_review(db, uuid.UUID(principal.subject), case_id, body.reason)
    except CaseNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ReviewNotAvailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"status": "review requested"}


@router.get("/console/evidence/{evidence_id}/thumbnail")
def evidence_thumbnail(
    principal: StaffOps, evidence_id: uuid.UUID, db: DB, cases: Cases
) -> Response:
    row = db.get(Evidence, evidence_id)
    if row is None or not row.thumb_uri:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thumbnail not found")
    data = cases.store.get(row.thumb_uri)  # sanitised copy: no metadata, re-encoded
    return Response(
        data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"}
    )


@router.get("/admin/config")
def admin_config(_: Annotated[Principal, require(Role.ADMIN)], cases: Cases) -> dict[str, Any]:
    """Read-only view of what drives decisions: policies, decision settings and the graph."""
    legal, merchant = load_policies(config_dir() / "policies")
    spec = cases.runner.registry.spec(get_settings().graph_active_version)
    return {
        "policies": [
            {
                "version": doc.version,
                "layer": doc.layer,
                "effective_from": doc.effective_from,
                "rules": [{"clause_id": r.clause_id, "text": r.text} for r in doc.rules],
            }
            for doc in [*legal, *merchant]
        ],
        "decision": load_decision_config(config_dir() / "decision.yaml").model_dump(),
        "graph": {
            "version": spec.version,
            "nodes": [
                {"id": n.id, "kind": n.kind, "task": n.task, "waits_for": n.waits_for}
                for n in spec.nodes
            ],
            "edges": [{"from": e.source, "to": e.target} for e in spec.edges],
        },
    }


@router.get("/console/reason-codes")
def reason_codes(principal: StaffOps) -> dict[str, list[str]]:
    """Single source of truth for the console's reason-code pickers."""
    return {
        **{k: sorted(v) for k, v in approvals.REASON_CODES.items()},
        "resolve": sorted(RESOLUTION_REASONS),
        "goodwill": sorted(goodwill.REASON_CODES),
        "review": sorted(REVIEW_REASONS),
    }


class TestOrderIn(BaseModel):
    phone: str = Field(min_length=10, max_length=20)
    sku: str = Field(min_length=3, max_length=64)
    qty: int = Field(default=1, ge=1, le=10)
    days_since_delivery: int = Field(default=3, ge=0, le=365)
    payment_method: Literal["upi", "card", "wallet", "cod"] = "upi"
    final_sale: bool = False


@router.post("/admin/test-orders", status_code=status.HTTP_201_CREATED)
def create_test_order(
    body: TestOrderIn, db: DB, admin: Annotated[Principal, require(Role.ADMIN)]
) -> dict[str, str]:
    """Demo helper: a delivered order for a signed-up customer (real orders come from the
    store's order system)."""
    digits = "".join(c for c in body.phone if c.isdigit())
    phone = f"+91{digits}" if len(digits) == 10 else f"+{digits}"
    customer = db.scalar(select(Customer).where(Customer.phone_index == blind_index(phone)))
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no customer has signed up with that number")
    product = db.scalar(select(Product).where(Product.sku == body.sku))
    if product is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown SKU {body.sku}")
    now = datetime.now(UTC)
    ref = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    order = Order(
        external_id=ref,
        customer_id=customer.id,
        placed_at=now - timedelta(days=body.days_since_delivery + 3),
        delivered_at=now - timedelta(days=body.days_since_delivery),
        status="delivered",
        payment_method=body.payment_method,
        coupon_minor=0,
        total_minor=product.price_minor * body.qty,
    )
    order.items = [
        OrderItem(
            external_id=f"{ref}-1",
            sku=product.sku,
            qty=body.qty,
            unit_price_minor=product.price_minor,
            discount_alloc_minor=0,
            final_sale=body.final_sale,
        )
    ]
    db.add(order)
    audit.append(
        db,
        actor_type="staff",
        actor_id=admin.subject,
        action="order.test_created",
        payload={"order": ref, "sku": product.sku},
    )
    db.commit()
    return {"order_id": ref}


@router.get("/admin/products")
def list_products(db: DB, _: Annotated[Principal, require(Role.ADMIN)]) -> list[dict[str, Any]]:
    return [
        {"sku": p.sku, "title": p.title, "category": p.category, "price_minor": p.price_minor}
        for p in db.scalars(select(Product).order_by(Product.sku))
    ]
