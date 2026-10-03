"""Payload schemas for the events that resume a waiting case. Invalid events are rejected
before they reach the graph, so a typo never escalates or corrupts a case."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerMessage(_Event):
    text: str = ""
    answers: dict[str, Any] = Field(default_factory=dict)


class CustomerUpload(_Event):
    files: list[str] = Field(min_length=1)


class CustomerConfirm(_Event):
    accept: bool
    option: str | None = None


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
