"""Vision assessment of evidence photos (DeepSeek-V4.1-Flash, structured output). The model
sees only the photos, the product and the claim; its output is schema-validated and
advisory — fusion decides how much it counts."""

import base64
from typing import Any, Literal

from pydantic import BaseModel, Field

from returns_agent.agent.context import prompt_refs, system_message
from returns_agent.llm.client import ImageInput, LLMClient, LLMRequest, complete_structured
from returns_agent.llm.prompts import load_prompt

MAX_IMAGES = 4


class VisionAssessment(BaseModel):
    matches_catalog_item: bool | None = None
    defect_type: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=120)
    severity: Literal["none", "minor", "moderate", "severe"] = "none"
    condition_grade: Literal["A", "B", "C", "D"]
    consistent_with_claim: bool | None = None
    missing_views: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0.0, le=1.0)


def assess(
    llm: LLMClient, facts: dict[str, Any], images: list[bytes]
) -> tuple[VisionAssessment, list[str]]:
    """`images` are sanitised JPEGs. Raises on an unusable answer (the caller falls back)."""
    prompt = load_prompt("assess_evidence")
    item = facts.get("item") or {}
    request_facts = facts.get("request") or {}
    context = {
        "product": {"sku": item.get("sku"), "category": item.get("category")},
        "claim": {
            "reason": request_facts.get("reason_category"),
            # The customer's own words stay out: they may carry instructions.
        },
    }
    request = LLMRequest(
        messages=[
            system_message(prompt, {"DECISION": context}),
            {"role": "user", "content": "(Assess the attached photos as json.)"},
        ],
        images=[
            ImageInput("image/jpeg", base64.b64encode(data).decode())
            for data in images[:MAX_IMAGES]
        ],
        thinking=True,
        reasoning_effort="low",
        max_tokens=1024,
    )
    return complete_structured(llm, request, VisionAssessment), prompt_refs(prompt)
