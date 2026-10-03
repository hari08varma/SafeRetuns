"""Schema v1. Money is integer minor units (paise); PII columns hold encrypted values."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class Entity(Base):
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def fk(target: str, nullable: bool = False) -> Mapped[Any]:
    return mapped_column(ForeignKey(target, ondelete="RESTRICT"), nullable=nullable, index=True)


# --- People -----------------------------------------------------------------------------


class StaffUser(Entity):
    __tablename__ = "staff_user"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    authority_limit_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")


class Customer(Entity):
    __tablename__ = "customer"
    external_id: Mapped[str] = mapped_column(String(64), unique=True)
    name_enc: Mapped[str] = mapped_column(Text)
    phone_enc: Mapped[str] = mapped_column(Text)
    phone_index: Mapped[str] = mapped_column(String(64), unique=True)  # blind index
    email_enc: Mapped[str] = mapped_column(Text)
    email_index: Mapped[str] = mapped_column(String(64), index=True)
    tier: Mapped[str] = mapped_column(String(16), default="standard")
    account_age_days: Mapped[int] = mapped_column(default=0)
    risk_profile: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Address(Entity):
    __tablename__ = "address"
    customer_id: Mapped[uuid.UUID] = fk("customer.id")
    city: Mapped[str] = mapped_column(String(64))
    pincode: Mapped[str] = mapped_column(String(12))
    address_enc: Mapped[str] = mapped_column(Text)


class OtpChallenge(Entity):
    __tablename__ = "otp_challenge"
    phone_index: Mapped[str] = mapped_column(String(64), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Catalogue & orders -----------------------------------------------------------------


class Product(Entity):
    __tablename__ = "product"
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32))
    variant: Mapped[str] = mapped_column(String(32))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    returnable: Mapped[bool]
    image_uris: Mapped[list[Any]] = mapped_column(default=list)


class Order(Entity):
    __tablename__ = "orders"
    __table_args__ = (CheckConstraint("total_minor >= 0", name="order_total_non_negative"),)
    external_id: Mapped[str] = mapped_column(String(64), unique=True)
    customer_id: Mapped[uuid.UUID] = fk("customer.id")
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    payment_method: Mapped[str] = mapped_column(String(16))
    coupon_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


class OrderItem(Entity):
    __tablename__ = "order_item"
    external_id: Mapped[str] = mapped_column(String(64), unique=True)
    order_id: Mapped[uuid.UUID] = fk("orders.id")
    sku: Mapped[str] = mapped_column(ForeignKey("product.sku"), index=True)
    qty: Mapped[int]
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    discount_alloc_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    final_sale: Mapped[bool] = mapped_column(default=False)
    order: Mapped[Order] = relationship(back_populates="items")


# --- Configuration (versioned) ----------------------------------------------------------


class Policy(Entity):
    __tablename__ = "policy"
    version: Mapped[str] = mapped_column(String(32), unique=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    yaml: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")


class PolicyRule(Entity):
    __tablename__ = "policy_rule"
    policy_id: Mapped[uuid.UUID] = fk("policy.id")
    clause_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    condition: Mapped[dict[str, Any]]


class Graph(Entity):
    __tablename__ = "graph"
    version: Mapped[str] = mapped_column(String(32), unique=True)
    spec: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_by: Mapped[uuid.UUID | None] = fk("staff_user.id", nullable=True)
    approved_by: Mapped[uuid.UUID | None] = fk("staff_user.id", nullable=True)


class PromptVersion(Entity):
    __tablename__ = "prompt_version"
    node_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))


class FeatureFlag(Entity):
    __tablename__ = "feature_flag"
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)


# --- Return cases ------------------------------------------------------------------------


class ReturnCase(Entity):
    __tablename__ = "return_case"
    __table_args__ = (Index("ix_return_case_status_sla", "status", "sla_due_at"),)
    customer_id: Mapped[uuid.UUID] = fk("customer.id")
    order_id: Mapped[uuid.UUID] = fk("orders.id")
    channel: Mapped[str] = mapped_column(String(16), default="web")
    current_node: Mapped[str] = mapped_column(String(64), default="START")
    graph_version: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="open")
    route: Mapped[str | None] = mapped_column(String(16))
    priority: Mapped[int] = mapped_column(default=0)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)  # optimistic lock
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __mapper_args__ = {"version_id_col": version}


class ReturnItem(Entity):
    __tablename__ = "return_item"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    order_item_id: Mapped[uuid.UUID] = fk("order_item.id")
    qty: Mapped[int]
    reason_category: Mapped[str | None] = mapped_column(String(32))
    reason_text: Mapped[str | None] = mapped_column(Text)
    condition_grade: Mapped[str | None] = mapped_column(String(1))
    resolution: Mapped[str | None] = mapped_column(String(32))


class Message(Entity):
    __tablename__ = "message"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    role: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16), default="web")
    content: Mapped[str] = mapped_column(Text)
    redacted_content: Mapped[str | None] = mapped_column(Text)


class Evidence(Entity):
    __tablename__ = "evidence"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    return_item_id: Mapped[uuid.UUID | None] = fk("return_item.id", nullable=True)
    uri: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(64))
    phash: Mapped[str | None] = mapped_column(String(32), index=True)
    exif: Mapped[dict[str, Any]] = mapped_column(default=dict)
    checks: Mapped[dict[str, Any]] = mapped_column(default=dict)
    assessment: Mapped[dict[str, Any]] = mapped_column(default=dict)


class DecisionRecord(Entity):
    __tablename__ = "decision_record"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    node: Mapped[str] = mapped_column(String(64))
    record: Mapped[dict[str, Any]]


class RiskAssessment(Entity):
    __tablename__ = "risk_assessment"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    score: Mapped[float]
    signals: Mapped[list[Any]] = mapped_column(default=list)


class Approval(Entity):
    __tablename__ = "approval"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    decision_id: Mapped[uuid.UUID] = fk("decision_record.id")
    requested_action: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approver_id: Mapped[uuid.UUID | None] = fk("staff_user.id", nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    token_hash: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Execution & lifecycle ----------------------------------------------------------------


class Shipment(Entity):
    __tablename__ = "shipment"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    carrier: Mapped[str] = mapped_column(String(32))
    awb: Mapped[str | None] = mapped_column(String(64))
    pickup_slot: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    events: Mapped[list[Any]] = mapped_column(default=list)


class Refund(Entity):
    __tablename__ = "refund"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="refund_amount_positive"),)
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    method: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    gateway_ref: Mapped[str | None] = mapped_column(String(64))


class ReplacementOrder(Entity):
    __tablename__ = "replacement_order"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    new_order_ref: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")


class Exchange(Entity):
    __tablename__ = "exchange"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    from_sku: Mapped[str] = mapped_column(String(64))
    to_sku: Mapped[str] = mapped_column(String(64))
    reservation_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")


class Outbox(Entity):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_status_next", "status", "next_attempt_at"),)
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    action: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]]
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Notification(Entity):
    __tablename__ = "notification"
    case_id: Mapped[uuid.UUID | None] = fk("return_case.id", nullable=True)
    channel: Mapped[str] = mapped_column(String(16))
    template: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SlaTimer(Entity):
    __tablename__ = "sla_timer"
    case_id: Mapped[uuid.UUID] = fk("return_case.id")
    kind: Mapped[str] = mapped_column(String(32))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Audit ---------------------------------------------------------------------------------


class AuditEvent(Base):
    """Append-only, hash-chained per chain (a case, or 'global' for non-case events)."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_chain_seq", "chain_key", "seq", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    chain_key: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # ai | staff | customer | system
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
