"""Vision assessment of evidence photos (DeepSeek-V4.1-Flash, structured output). The
model describes condition and consistency; it never decides eligibility, money or routing."""

import base64
import io
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field

from returns_agent.agent.context import prompt_refs, system_message
from returns_agent.llm.client import ImageInput, LLMClient, LLMRequest, complete_structured
from returns_agent.llm.prompts import load_prompt

MAX_IMAGES = 4
MAX_SIDE_PX = 1600
View = Literal[
    "full_item",
    "close_up_of_defect",
    "label_or_tag",
    "packaging",
    "serial_number",
    "all_items_received",
]


class VisionAssessment(BaseModel):
    matches_catalog_item: bool
    defect_type: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=80)
    severity: Literal["none", "minor", "moderate", "severe"]
    condition_grade: Literal["A", "B", "C", "D"]
    consistent_with_claim: bool
    missing_views: list[View] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0, le=1)


def prepare_image(data: bytes) -> ImageInput:
    """Downscale to a size the model reads well; re-encoding also drops all metadata."""
    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
    rgb.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=85)
    return ImageInput(media_type="image/jpeg", data_b64=base64.b64encode(out.getvalue()).decode())


def claim_view(facts: dict[str, Any]) -> dict[str, Any]:
    """What the model is told about the claim: item and reason only, no customer data."""
    item = facts.get("item") or {}
    request = facts.get("request") or {}
    return {
        "item": {"sku": item.get("sku"), "category": item.get("category")},
        "reason_category": request.get("reason_category"),
    }


def assess_photos(
    llm: LLMClient, photos: list[bytes], facts: dict[str, Any]
) -> tuple[VisionAssessment, list[str]]:
    prompt = load_prompt("assess_evidence")
    request = LLMRequest(
        messages=[
            system_message(prompt, {"CLAIM": claim_view(facts)}),
            {"role": "user", "content": "Photos of the item are attached."},
        ],
        images=[prepare_image(p) for p in photos[:MAX_IMAGES]],
        thinking=True,
        reasoning_effort="high",
        max_tokens=2048,
    )
    return complete_structured(llm, request, VisionAssessment), prompt_refs(prompt)
