"""INV-6: a customer message may not commit to anything the decision does not contain.
Deterministic checks first (amounts with or without a currency marker, amounts leaked in
paise, unexplained numbers, placeholders, options, guarantees, timelines), then an LLM
check for implied promises. The deterministic layer alone must stop every money error."""

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
# A bare number of 3+ digits (any script) that is not part of a word, code or amount.
_BARE_NUMBER = re.compile(r"(?<![\w.,₹/-])(\d[\d,]*(?:\.\d+)?)(?![\w/-]|\.\d)")
PLACEHOLDER = re.compile(r"<[A-Z][A-Z_]*_\d+>")
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


def deterministic_violations(
    message: str, decision: dict[str, Any], allowed_tokens: frozenset[str] = frozenset()
) -> list[str]:
    """`allowed_tokens`: redaction placeholders the system itself created (restored later)."""
    violations: list[str] = []
    allowed_amounts = {int(a) for a in decision.get("amounts_minor", [])}
    covered: list[tuple[int, int]] = []
    for match in _AMOUNT.finditer(message):
        covered.append(match.span())
        amount = _to_paise(match.group(1) or match.group(2))
        if amount not in allowed_amounts:
            violations.append(f"amount not in decision: {amount / 100:.2f}")
    allowed_days = {int(d) for d in decision.get("timeline_days", [])}
    for match in _TIMELINE.finditer(message):
        covered.append(match.span())
        if int(match.group(1)) not in allowed_days:
            violations.append(f"timeline not in decision: {match.group(0)}")
    for match in _BARE_NUMBER.finditer(message):
        raw = match.group(1).rstrip(",")
        integer = raw.replace(",", "").split(".")[0]
        if len(integer) < 3 or any(a <= match.start() < b for a, b in covered):
            continue
        if _to_paise(raw) in allowed_amounts:
            continue  # the decided amount without a currency sign
        if int(integer) in allowed_amounts:
            violations.append(
                f"amount written in paise ({raw}); write it exactly as in DECISION.amounts"
            )
        else:
            violations.append(f"number not in decision: {raw}")
    for token in sorted(set(PLACEHOLDER.findall(message)) - allowed_tokens):
        violations.append(
            f"placeholder {token} is not allowed; write the value from DECISION or leave it out"
        )
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


def verify(
    llm: LLMClient | None,
    message: str,
    decision: dict[str, Any],
    allowed_tokens: frozenset[str] = frozenset(),
) -> list[str]:
    violations = deterministic_violations(message, decision, allowed_tokens)
    if violations or llm is None:
        return violations
    shown = {k: v for k, v in decision.items() if k != "amounts_minor"}  # models see rupees
    verdict = complete_structured(
        llm,
        LLMRequest(
            messages=[
                system_message(load_prompt("verify"), {"DECISION": shown, "MESSAGE": message}),
                {"role": "user", "content": "Return the json verdict now."},
            ],
            max_tokens=512,
        ),
        Verdict,
    )
    return verdict.violations
