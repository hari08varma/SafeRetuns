"""Simulated customers. Scripted customers replay fixed lines (deterministic, no model);
LLM customers play a persona around the case's goal. Confirmations are deterministic in
both: the UI buttons are not what is being evaluated."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from returns_agent.evals.cases import PERSONAS, EvalCase, evals_dir
from returns_agent.llm.client import LLMClient, LLMRequest, complete_structured


class Customer(Protocol):
    def opening(self) -> str: ...

    def reply(self, transcript: list[dict[str, str]]) -> str | None:
        """Next message, or None when the customer has nothing more to say (goes silent)."""
        ...


@dataclass
class ScriptedCustomer:
    case: EvalCase
    pii: dict[str, str] = field(default_factory=dict)  # fills {phone} / {email} in lines
    _next: int = 0

    def opening(self) -> str:
        return self._fill(self.case.goal.story)

    def reply(self, transcript: list[dict[str, str]]) -> str | None:
        lines = self.case.goal.follow_ups
        if self._next >= len(lines):
            return None
        self._next += 1
        return self._fill(lines[self._next - 1])

    def _fill(self, text: str) -> str:
        for key, value in self.pii.items():
            text = text.replace("{" + key + "}", value)
        return text


class _Turn(BaseModel):
    message: str = ""
    done: bool = False


@dataclass
class LLMCustomer:
    case: EvalCase
    persona: str
    llm: LLMClient
    title: str = ""

    def opening(self) -> str:
        return self._turn([]) or self.case.goal.story

    def reply(self, transcript: list[dict[str, str]]) -> str | None:
        return self._turn(transcript)

    def _turn(self, transcript: list[dict[str, str]]) -> str | None:
        # The simulator speaks as "assistant"; the support agent's lines are its "user" input.
        history = [
            {"role": "assistant" if m["role"] == "customer" else "user", "content": m["text"]}
            for m in transcript
        ]
        if not history:
            history = [{"role": "user", "content": "(Start the chat with your first message.)"}]
        request = LLMRequest(
            messages=[{"role": "system", "content": self.system_prompt()}, *history],
            max_tokens=300,
        )
        turn = complete_structured(self.llm, request, _Turn)
        return None if turn.done or not turn.message.strip() else turn.message.strip()

    def system_prompt(self) -> str:
        g, o = self.case.goal, self.case.order
        desired = g.desired_resolution or "whatever the store offers"
        if g.exchange_sku:
            desired += f" (you want {g.exchange_sku} instead)"
        delivered = (
            f"{o.delivered_days_ago} days ago" if o.delivered_days_ago is not None else "not yet"
        )
        template = (evals_dir() / "simulator" / "customer.md").read_text()
        template = template.split("-->", 1)[1].strip() if template.startswith("<!--") else template
        return template.format(
            item=f"{self.title or o.sku} (SKU {o.sku}), quantity {g.qty_returning}",
            delivered=delivered,
            payment={"cod": "cash on delivery"}.get(o.payment_method, o.payment_method),
            story=g.story,
            reason=(g.reason_category or "not sure").replace("_", " "),
            desired=desired,
            gift="yes" if g.is_gift else "no",
            persona=PERSONAS[self.persona],
        )


def confirm_choice(case: EvalCase, options: list[str]) -> dict[str, Any] | None:
    """What the customer clicks on the offer screen; None means they decline."""
    g = case.goal
    if not g.accepts:
        return None
    choice: dict[str, Any] = {"accept": True}
    if g.desired_resolution == "refund" and "keep_item_refund" in options:
        choice["option"] = "keep_item_refund"  # nobody prefers shipping an item back
    elif g.desired_resolution in options:
        choice["option"] = g.desired_resolution
    elif not g.accepts_alternative:
        return None
    if g.refund_method:
        choice["refund_method"] = g.refund_method
    if g.exchange_sku:
        choice["exchange_sku"] = g.exchange_sku
    return choice
