"""Provider-independent LLM interface.

Business code depends only on `LLMClient`; providers (DeepSeek, Fake) implement it.
"""

import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class ImageInput:
    media_type: str  # e.g. "image/png"
    data_b64: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMRequest:
    messages: list[dict[str, Any]]  # [{"role": "system"|"user"|"assistant", "content": str}]
    images: list[ImageInput] = field(default_factory=list)  # attached to the last user message
    json_output: bool = False
    tools: list[dict[str, Any]] | None = None
    thinking: bool = False
    reasoning_effort: Literal["low", "high", "max"] | None = None  # used only when thinking
    max_tokens: int = 4096
    timeout_s: float | None = None


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class StructuredOutputError(Exception):
    """The model did not return valid output for the schema after all retries."""


M = TypeVar("M", bound=BaseModel)


def complete_structured(
    client: LLMClient, request: LLMRequest, schema: type[M], retries: int = 1
) -> M:
    """Request JSON output and validate it against `schema`.

    On failure the validation error is sent back to the model and the call is retried.
    Never trust JSON mode alone: every output is validated.
    """
    # DeepSeek JSON mode guarantees valid JSON, not the schema, and requires the word
    # "json" plus an example in the prompt; so the schema is always included here.
    instruction = "Respond only with a json object matching this JSON schema: " + json.dumps(
        schema.model_json_schema()
    )
    messages = list(request.messages)
    if messages and messages[0]["role"] == "system":
        messages[0] = {**messages[0], "content": f"{messages[0]['content']}\n\n{instruction}"}
    else:
        messages.insert(0, {"role": "system", "content": instruction})
    req = replace(request, json_output=True, messages=messages)
    last_error = ""
    for _ in range(retries + 1):
        response = client.complete(req)
        try:
            return schema.model_validate(json.loads(response.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            req = replace(
                req,
                messages=[
                    *req.messages,
                    {"role": "assistant", "content": response.text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply was not valid. Return only JSON matching the "
                            f"schema. Error: {last_error}"
                        ),
                    },
                ],
            )
    raise StructuredOutputError(last_error)
