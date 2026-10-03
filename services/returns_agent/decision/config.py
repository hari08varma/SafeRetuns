"""Decision settings with hard limits. Admins tune values inside the limits; nothing
(including the self-improvement loop) can push the gate past them."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Hard limits for the autonomy gate.
MAX_AUTO_RISK = 0.5
MAX_AUTO_VALUE_MINOR = 2_500_000  # ₹25,000
MIN_AUTO_CONFIDENCE = 0.7
MAX_ESCALATE_RISK = 0.8
MIN_ESCALATE_CONFIDENCE = 0.5


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revenue: float = Field(ge=0, le=1)
    cost: float = Field(ge=0, le=1)
    customer_fit: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reverse_shipping: int = Field(ge=0)
    handling: int = Field(ge=0)
    forward_shipping: int = Field(ge=0)


class RiskWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    high_return_ratio: float = Field(ge=0, le=1)
    serial_returner: float = Field(ge=0, le=1)
    new_account: float = Field(ge=0, le=1)
    high_value: float = Field(ge=0, le=1)
    same_day_return: float = Field(ge=0, le=1)
    cod_high_value: float = Field(ge=0, le=1)
    # Account and history signals (Phase 8)
    linked_accounts: float = Field(ge=0, le=1)
    prior_confirmed_fraud: float = Field(ge=0, le=1)
    serial_damage_claims: float = Field(ge=0, le=1)
    # Evidence signals (from deterministic checks and the vision assessment)
    duplicate_photo_other_customer: float = Field(ge=0, le=1)
    catalogue_photo: float = Field(ge=0, le=1)
    reused_own_photo: float = Field(ge=0, le=1)
    photo_before_delivery: float = Field(ge=0, le=1)
    ai_generated_marker: float = Field(ge=0, le=1)
    edited_photo: float = Field(ge=0, le=1)
    reason_evidence_mismatch: float = Field(ge=0, le=1)
    item_mismatch: float = Field(ge=0, le=1)
    high_value_minor: int = Field(gt=0)
    cod_high_value_minor: int = Field(gt=0)


class Gate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_max_risk: float = Field(gt=0, le=MAX_AUTO_RISK)
    auto_max_value_minor: int = Field(gt=0, le=MAX_AUTO_VALUE_MINOR)
    auto_min_confidence: float = Field(ge=MIN_AUTO_CONFIDENCE, le=1)
    escalate_min_risk: float = Field(gt=0, le=MAX_ESCALATE_RISK)
    escalate_below_confidence: float = Field(ge=MIN_ESCALATE_CONFIDENCE, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "Gate":
        if self.auto_max_risk > self.escalate_min_risk:
            raise ValueError("auto_max_risk must not exceed escalate_min_risk")
        if self.escalate_below_confidence > self.auto_min_confidence:
            raise ValueError("escalate_below_confidence must not exceed auto_min_confidence")
        return self


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    weights: Weights
    costs_minor: Costs
    recovery_rate: dict[str, float]
    risk: RiskWeights
    gate: Gate
    kill_switch: bool = False

    @model_validator(mode="after")
    def rates(self) -> "DecisionConfig":
        if "default" not in self.recovery_rate:
            raise ValueError("recovery_rate needs a default")
        if any(not 0 <= r <= 1 for r in self.recovery_rate.values()):
            raise ValueError("recovery rates must be between 0 and 1")
        return self

    def recovery(self, category: str) -> float:
        return self.recovery_rate.get(category, self.recovery_rate["default"])


def load_decision_config(path: Path) -> DecisionConfig:
    return DecisionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
