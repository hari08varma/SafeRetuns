"""Payload schemas for the events that resume a waiting case. Invalid events are rejected
before they reach the graph, so a typo never escalates or corrupts a case."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerMessage(_Event):
    text: str = ""
    answers: dict[str, Any] = Field(default_factory=dict)
    timed_out: bool = False  # sent by the inactivity timer, never by customers


class CustomerUpload(_Event):
    files: list[str] = Field(default_factory=list)
    timed_out: bool = False

    @model_validator(mode="after")
    def files_or_timeout(self) -> "CustomerUpload":
        if not self.files and not self.timed_out:
            raise ValueError("at least one file is required")
        return self


class CustomerConfirm(_Event):
    accept: bool
    option: str | None = None
    refund_method: Literal["source", "bank_transfer", "upi", "store_credit"] | None = None
    exchange_sku: str | None = None
    timed_out: bool = False


class ActionResult(_Event):
    action: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class Approval(_Event):
    decision: Literal["approve", "modify", "reject"]
    approver_id: str
    token: str | None = None
    reason_code: str | None = None
    option: str | None = None


class CarrierEvent(_Event):
    event: Literal["picked_up", "in_transit", "received", "pickup_failed"]
    awb: str | None = None


class QcResult(_Event):
    passed: bool
    grade: Literal["A", "B", "C", "D"] | None = None


class HumanResolution(_Event):
    outcome: Literal["resolved_by_human", "rejected", "cancelled"]
    staff_id: str


SCHEMAS: dict[str, type[_Event]] = {
    "customer_message": CustomerMessage,
    "customer_upload": CustomerUpload,
    "customer_confirm": CustomerConfirm,
    "approval": Approval,
    "carrier_event": CarrierEvent,
    "qc_result": QcResult,
    "human_resolution": HumanResolution,
    "action_result": ActionResult,
}


class InvalidEvent(ValueError):
    pass


def validate_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    schema = SCHEMAS.get(event_type)
    if schema is None:
        raise InvalidEvent(f"unknown event type {event_type!r}")
    try:
        return schema.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidEvent(str(exc)) from exc
