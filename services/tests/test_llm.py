import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from returns_agent.llm import ImageInput, LLMRequest, StructuredOutputError, complete_structured
from returns_agent.llm.deepseek import DeepSeekProvider
from returns_agent.llm.fake import FakeProvider


class Extraction(BaseModel):
    item: str
    reason_category: str


REQ = LLMRequest(messages=[{"role": "user", "content": "kurta too tight"}])


def test_structured_output_valid_first_try() -> None:
    fake = FakeProvider(['{"item": "kurta", "reason_category": "size_fit"}'])
    result = complete_structured(fake, REQ, Extraction)
    assert result == Extraction(item="kurta", reason_category="size_fit")
    assert fake.calls[0].json_output is True


def test_structured_output_retries_with_error_feedback() -> None:
    fake = FakeProvider(["not json", '{"item": "kurta", "reason_category": "size_fit"}'])
    result = complete_structured(fake, REQ, Extraction)
    assert result.item == "kurta"
    retry_messages = fake.calls[1].messages
    assert retry_messages[-2] == {"role": "assistant", "content": "not json"}
    assert "not valid" in retry_messages[-1]["content"]


def test_structured_output_gives_up_after_retries() -> None:
    fake = FakeProvider(['{"item": "kurta"}', '{"item": "kurta"}'])  # missing field twice
    with pytest.raises(StructuredOutputError):
        complete_structured(fake, REQ, Extraction, retries=1)


def test_fake_provider_raises_when_exhausted() -> None:
    with pytest.raises(RuntimeError):
        FakeProvider().complete(REQ)


def _provider(handler: object, **kwargs: object) -> DeepSeekProvider:
    return DeepSeekProvider(
        api_key="k", model="m", transport=httpx.MockTransport(handler), **kwargs
    )  # type: ignore[arg-type]


def _ok(content: str = "hi", tool_calls: list[object] | None = None) -> httpx.Response:
    message = {"content": content, "tool_calls": tool_calls}
    return httpx.Response(
        200, json={"model": "m", "choices": [{"message": message}], "usage": {"total_tokens": 3}}
    )


def test_deepseek_builds_request_and_parses_tool_calls() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers["authorization"]
        return _ok(
            "", [{"id": "c1", "function": {"name": "get_order", "arguments": '{"order_id": "O1"}'}}]
        )

    resp = _provider(handler).complete(
        LLMRequest(
            messages=[{"role": "user", "content": "check"}],
            json_output=True,
            tools=[{"type": "function"}],
            images=[ImageInput("image/png", "QUJD")],
            thinking=True,
            reasoning_effort="low",
            max_tokens=512,
        )
    )

    assert seen["auth"] == "Bearer k"
    assert seen["model"] == "m"
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["thinking"] == {"type": "enabled"}
    assert seen["reasoning_effort"] == "low"
    assert seen["max_tokens"] == 512
    content = seen["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "check"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert resp.tool_calls[0].name == "get_order"
    assert resp.tool_calls[0].arguments == {"order_id": "O1"}


def test_deepseek_disables_thinking_explicitly() -> None:
    # Thinking is ON by default in the DeepSeek API, so non-thinking calls must say so.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _ok()

    _provider(handler).complete(REQ)
    assert seen["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in seen
    assert "response_format" not in seen


def test_structured_prompt_contains_json_and_schema() -> None:
    # DeepSeek JSON mode requires the word "json" and an example/schema in the prompt.
    fake = FakeProvider(['{"item": "kurta", "reason_category": "size_fit"}'])
    complete_structured(
        fake,
        LLMRequest(
            messages=[
                {"role": "system", "content": "You are a returns assistant."},
                {"role": "user", "content": "kurta too tight"},
            ]
        ),
        Extraction,
    )
    system = fake.calls[0].messages[0]
    assert system["role"] == "system" and system["content"].startswith("You are a returns")
    assert "json" in system["content"] and '"reason_category"' in system["content"]


def test_deepseek_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("returns_agent.llm.deepseek.time.sleep", lambda _: None)
    responses = [httpx.Response(429), _ok("done")]
    provider = _provider(lambda request: responses.pop(0))
    assert provider.complete(REQ).text == "done"


def test_deepseek_does_not_retry_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("returns_agent.llm.deepseek.time.sleep", lambda _: None)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": "bad"})

    with pytest.raises(httpx.HTTPStatusError):
        _provider(handler).complete(REQ)
    assert len(calls) == 1


def test_deepseek_requires_api_key() -> None:
    with pytest.raises(ValueError):
        DeepSeekProvider(api_key="", model="m")
