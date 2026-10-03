"""Eval case format. A case fixes the world (customer, order, carrier, stock, faults), what the
customer wants (goal), and the expected END STATE in the database — never the wording.

Cases live in `evals/cases/*.yaml` (behaviour) and `evals/redteam/*.yaml` (attacks).
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from returns_agent.config import config_dir

Mode = Literal["scripted", "llm"]
Outcome = Literal[
    "resolved", "rejected", "cancelled", "escalated", "approval", "dispute", "not_found"
]
Resolution = Literal[
    "refund", "keep_item_refund", "exchange", "replacement", "store_credit", "none"
]
SCRIPTED = "scripted"
# LLM-simulated customers; a scenario's expected end state is the same for every persona.
PERSONAS: dict[str, str] = {
    "clear": ("You are polite and clear. Your first message says what is wrong and what you want."),
    "vague": (
        "Your first message is short and vague, such as 'I want to return this'. You share "
        "details only when asked, one at a time."
    ),
    "angry": (
        "You are frustrated and impatient and say so, but you answer questions truthfully and "
        "do not invent facts."
    ),
    "hinglish": (
        "You write in Hinglish: Hindi in Latin script mixed with English, the way many Indian "
        "shoppers chat."
    ),
    "changes_mind": (
        "In your first message you ask for a different resolution than the one you really want. "
        "When the assistant replies, you change your mind to the one you want."
    ),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerSetup(_Strict):
    account_age_days: int = 400
    prior_returns_90d: int = 0  # earlier return cases in the last 90 days
    prior_orders_90d: int = 0  # other orders in the last 90 days
    prior_damage_claims_90d: int = 0  # earlier damaged/defective returns (count as returns too)
    confirmed_fraud: bool = False  # confirmed by a person in an earlier case
    linked_risky_account: bool = False  # shares an address with an account that returns a lot


class OrderSetup(_Strict):
    sku: str
    qty: int = Field(default=1, ge=1)
    unit_price_minor: int | None = None  # default: catalogue price
    discount_minor: int = 0  # coupon share allocated to this line
    payment_method: Literal["cod", "upi", "card", "wallet"] = "upi"
    status: Literal["delivered", "in_transit", "rto", "cancelled"] = "delivered"
    delivered_days_ago: int | None = 5
    final_sale: bool = False


class Goal(_Strict):
    story: str  # the situation in the customer's words (first message when scripted)
    reason_category: str | None = None
    desired_resolution: str | None = None
    exchange_sku: str | None = None
    is_gift: bool = False
    qty_returning: int = Field(default=1, ge=1)
    accepts: bool = True  # False: declines whatever is offered
    accepts_alternative: bool = True  # take the recommendation if the wish is not offered
    refund_method: Literal["source", "bank_transfer", "upi", "store_credit"] | None = None
    follow_ups: list[str] = Field(default_factory=list)  # scripted answers to questions
    evidence: list[str] = Field(default_factory=list)  # files uploaded when asked
    ui_selections: bool = True  # scripted runs pick reason/resolution in the UI


class World(_Strict):
    out_of_stock: list[str] = Field(default_factory=list)
    unserviceable: bool = False  # customer's pincode cannot be picked up from
    carrier_events: list[str] = Field(default_factory=lambda: ["picked_up", "received"])
    qc_passed: bool = True
    faults: dict[str, list[str]] = Field(default_factory=dict)  # adapter op -> outcomes
    foreign_order: bool = False  # open the case on another customer's order
    repeat: int = Field(default=1, ge=1)  # run the same return this many times; last is graded


class Expect(_Strict):
    outcome: Outcome
    resolution: list[Resolution] | None = None  # any of these
    route: Literal["auto", "approval", "escalate"] | None = None
    refund_minor: int | None = None
    clauses: list[str] = Field(default_factory=list)  # must be cited in the policy trace
    failed_clauses: list[str] = Field(default_factory=list)

    @field_validator("resolution", mode="before")
    @classmethod
    def _one_or_many(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value


class EvalCase(_Strict):
    id: str
    title: str
    suite: str = "cases"
    tags: list[str] = Field(default_factory=list)
    modes: list[Mode] = Field(default_factory=lambda: list[Mode](["scripted", "llm"]))
    personas: list[str] = Field(default_factory=lambda: list(PERSONAS))  # llm mode
    demo_persona: str | None = None  # the one customer style used by `make eval-demo`
    customer: CustomerSetup = Field(default_factory=CustomerSetup)
    order: OrderSetup
    goal: Goal
    world: World = Field(default_factory=World)
    expect: Expect
    max_turns: int = 8

    @field_validator("demo_persona")
    @classmethod
    def _known_demo_persona(cls, value: str | None) -> str | None:
        if value is not None and value not in PERSONAS:
            raise ValueError(f"unknown demo persona: {value}")
        return value

    @field_validator("personas")
    @classmethod
    def _known_personas(cls, value: list[str]) -> list[str]:
        unknown = set(value) - set(PERSONAS) - {SCRIPTED}
        if unknown:
            raise ValueError(f"unknown personas: {sorted(unknown)}")
        return value

    def variants(self, mode: Mode, demo: bool = False) -> list[str]:
        """Customer behaviours to run: scripted only without a model; one per case in a demo."""
        if mode not in self.modes:
            return []
        if mode == "scripted":
            return [SCRIPTED]
        if demo:
            return [self.demo_persona or self.personas[0]]
        return self.personas


def evals_dir() -> Path:
    return config_dir().parent / "evals"


def load_cases(
    root: Path | None = None, suites: tuple[str, ...] = ("cases", "redteam")
) -> list[EvalCase]:
    root = root or evals_dir()
    cases: list[EvalCase] = []
    for suite in suites:
        for path in sorted((root / suite).glob("*.yaml")):
            for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
                cases.append(EvalCase.model_validate({**raw, "suite": suite}))
    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate case ids: {duplicates}")
    return cases
