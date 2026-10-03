"""Extract the request from the conversation. Three independent samples are compared;
low agreement means the agent asks instead of guessing."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from pydantic import BaseModel

from returns_agent.agent.context import (
    conversation_messages,
    prompt_refs,
    redactor_for,
    system_message,
)
from returns_agent.llm.client import LLMClient, LLMRequest, complete_structured
from returns_agent.llm.prompts import load_prompt

SAMPLES = 3
MIN_AGREEMENT = 2 / 3


class Extraction(BaseModel):
    reason_category: (
        Literal[
            "size_fit",
            "damaged",
            "defective",
            "wrong_item",
            "not_as_described",
            "changed_mind",
            "other",
        ]
        | None
    ) = None
    desired_resolution: Literal["refund", "exchange", "replacement", "store_credit"] | None = None
    exchange_variant: str | None = None
    is_gift: bool = False
    language: str = "en"
    sentiment: Literal["calm", "frustrated", "angry"] = "calm"
    wants_human: bool = False
    legal_threat: bool = False


class Understanding(BaseModel):
    extraction: Extraction
    agreement: float
    prompt_refs: list[str]


def understand(llm: LLMClient, facts: dict[str, Any], samples: int = SAMPLES) -> Understanding:
    prompt = load_prompt("understand_request")
    redactor = redactor_for(facts)
    request = LLMRequest(
        messages=[
            system_message(prompt),
            *conversation_messages(facts.get("conversation") or [], redactor),
        ],
        thinking=True,
        reasoning_effort="low",
        max_tokens=1024,
    )
    with ThreadPoolExecutor(max_workers=samples) as pool:
        results = list(
            pool.map(lambda _: complete_structured(llm, request, Extraction), range(samples))
        )

    keys = [(r.reason_category, r.desired_resolution) for r in results]
    winner, votes = Counter(keys).most_common(1)[0]
    majority = next(r for r, k in zip(results, keys, strict=True) if k == winner)
    merged = majority.model_copy(
        update={
            # Safety flags: if any sample saw them, keep them.
            "wants_human": any(r.wants_human for r in results),
            "legal_threat": any(r.legal_threat for r in results),
        }
    )
    return Understanding(
        extraction=merged, agreement=votes / samples, prompt_refs=prompt_refs(prompt)
    )
