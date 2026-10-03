"""Return policy as versioned YAML: legal rules (locked) and merchant rules (admin-owned)."""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Resolution = Literal["refund", "exchange", "replacement", "store_credit"]
RefundMethod = Literal["source", "bank_transfer", "upi", "store_credit"]
Evidence = Literal["photo", "video", "invoice"]

ALL_RESOLUTIONS: frozenset[str] = frozenset({"refund", "exchange", "replacement", "store_credit"})
ALL_REFUND_METHODS: frozenset[str] = frozenset({"source", "bank_transfer", "upi", "store_credit"})


class Effects(BaseModel):
    """Applied when a rule applies (and its requirement passes)."""

    model_config = ConfigDict(extra="forbid")

    allow_resolutions: list[Resolution] | None = None  # intersected across rules
    refund_methods: list[RefundMethod] | None = None  # intersected across rules
    require_evidence: list[Evidence] = Field(default_factory=list)  # unioned
    restocking_fee_pct: int = Field(default=0, ge=0, le=100)  # max across rules
    shipping_refundable: bool | None = None  # False wins
    # Legal rules only:
    guarantee_resolutions: list[Resolution] = Field(default_factory=list)
    waive_fees: bool = False


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str
    text: str
    applies_to: Any = True  # JSONLogic; rule is skipped when false
    require: Any = None  # JSONLogic; when false the item is ineligible
    reason_code: str | None = None  # reported when `require` fails
    legal_overridable: bool = True  # False = legal protection cannot override this rule
    effects: Effects = Field(default_factory=Effects)


class PolicyDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    layer: Literal["legal", "merchant"]
    effective_from: datetime
    return_window_days: int | None = None  # merchant only
    rules: list[Rule]


class PolicyError(ValueError):
    pass


def load_policy(path: str | Path) -> PolicyDoc:
    doc = PolicyDoc.model_validate(yaml.safe_load(Path(path).read_text()))
    ids = [r.clause_id for r in doc.rules]
    if len(ids) != len(set(ids)):
        raise PolicyError(f"duplicate clause ids in {path}")
    for rule in doc.rules:
        if rule.require is not None and not rule.reason_code:
            raise PolicyError(f"{rule.clause_id}: rules with `require` need a reason_code")
        if doc.layer == "merchant" and (
            rule.effects.guarantee_resolutions or rule.effects.waive_fees
        ):
            raise PolicyError(f"{rule.clause_id}: only legal rules may guarantee or waive")
    if doc.layer == "merchant" and doc.return_window_days is None:
        raise PolicyError("merchant policy needs return_window_days")
    return doc


def load_policies(directory: str | Path) -> tuple[list[PolicyDoc], list[PolicyDoc]]:
    """Returns (legal, merchant) documents from a directory of YAML files."""
    docs = [load_policy(p) for p in sorted(Path(directory).glob("*.yaml"))]
    return [d for d in docs if d.layer == "legal"], [d for d in docs if d.layer == "merchant"]


def select_version(docs: list[PolicyDoc], at: datetime) -> PolicyDoc:
    """The version in force at `at` (for merchant policy: the order date)."""
    candidates = [d for d in docs if d.effective_from <= at]
    if not candidates:
        raise PolicyError(f"no policy version in force at {at.isoformat()}")
    return max(candidates, key=lambda d: d.effective_from)
