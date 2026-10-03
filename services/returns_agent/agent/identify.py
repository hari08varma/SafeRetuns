"""Match the conversation to one of the customer's purchased items."""

from typing import Any

from pydantic import BaseModel, Field

from returns_agent.agent.context import (
    conversation_messages,
    prompt_refs,
    redactor_for,
    system_message,
)
from returns_agent.llm.client import LLMClient, LLMRequest, complete_structured
from returns_agent.llm.prompts import load_prompt

MIN_CONFIDENCE = 0.7


class ItemMatch(BaseModel):
    item_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


def identify_item(llm: LLMClient, facts: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Returns (item id or None if unclear, prompt refs). Never returns an id that was
    not among the candidates."""
    prompt = load_prompt("identify_order")
    candidates: list[dict[str, Any]] = facts.get("candidates") or []
    redactor = redactor_for(facts)
    match = complete_structured(
        llm,
        LLMRequest(
            messages=[
                system_message(prompt, {"CANDIDATES": candidates}),
                *conversation_messages(facts.get("conversation") or [], redactor),
            ],
            max_tokens=256,
        ),
        ItemMatch,
    )
    valid = {c["item_id"] for c in candidates}
    ok = match.item_id in valid and match.confidence >= MIN_CONFIDENCE
    return (match.item_id if ok else None), prompt_refs(prompt)
