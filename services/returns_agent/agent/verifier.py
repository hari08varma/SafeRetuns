"""INV-6: a customer message may not commit to anything the decision does not contain.
Deterministic checks first (amounts, options, guarantees, timelines), then an LLM check
for implied promises."""

import re
from typing import Any

from pydantic import BaseModel

from returns_agent.agent.context import system_message
from returns_agent.llm.client import LLMClient, LLMRequest, complete_structured
from returns_agent.llm.prompts import load_prompt

_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*(?:rupees|inr)",
    re.IGNORECASE,
)
_TIMELINE = re.compile(
    r"\b(?:within|in)\s+(\d+)\s*(?:business\s+)?(?:hours?|days?|weeks?)\b", re.IGNORECASE
)
_GUARANTEE = re.compile(r"\b(guarantee[ds]?|promise[ds]?|definitely|100\s*%)", re.IGNORECASE)
_OPTION_WORDS = {
    "replacement": re.compile(r"\breplac(?:e|ement)\b", re.IGNORECASE),
    "exchange": re.compile(r"\bexchang(?:e|ed)\b", re.IGNORECASE),
    "store_credit": re.compile(r"\bstore\s+credit\b", re.IGNORECASE),
    "refund": re.compile(r"\brefund", re.IGNORECASE),
}


class Verdict(BaseModel):
    violations: list[str]


def _to_paise(raw: str) -> int:
    value = raw.replace(",", "")
    rupees, _, paise = value.partition(".")
    return int(rupees) * 100 + int((paise + "00")[:2])


def deterministic_violations(message: str, decision: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    allowed_amounts = {int(a) for a in decision.get("amounts_minor", [])}
    for match in _AMOUNT.finditer(message):
        amount = _to_paise(match.group(1) or match.group(2))
        if amount not in allowed_amounts:
            violations.append(f"amount not in decision: {amount / 100:.2f}")
    allowed_days = {int(d) for d in decision.get("timeline_days", [])}
    for match in _TIMELINE.finditer(message):
        if int(match.group(1)) not in allowed_days:
            violations.append(f"timeline not in decision: {match.group(0)}")
    if _GUARANTEE.search(message):
        violations.append("guarantee language")
    if decision.get("situation") == "offer":
        offered = set(decision.get("options", []))
        for option, pattern in _OPTION_WORDS.items():
            if pattern.search(message) and option not in offered:
                if option == "refund" and "keep_item_refund" in offered:
                    continue
                violations.append(f"option not offered: {option}")
    return violations


def verify(llm: LLMClient | None, message: str, decision: dict[str, Any]) -> list[str]:
    violations = deterministic_violations(message, decision)
    if violations or llm is None:
        return violations
    verdict = complete_structured(
        llm,
        LLMRequest(
            messages=[
                system_message(load_prompt("verify"), {"DECISION": decision, "MESSAGE": message}),
                {"role": "user", "content": "Return the json verdict now."},
            ],
            max_tokens=512,
        ),
        Verdict,
    )
    return verdict.violations
