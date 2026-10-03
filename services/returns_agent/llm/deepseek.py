"""DeepSeek provider over the OpenAI-compatible Chat Completions API.

Request format follows the official docs (https://api-docs.deepseek.com/):
- thinking: {"type": "enabled" | "disabled"}; it is ON by default, so always sent explicitly
- reasoning_effort: "low" | "high" | "max" (thinking mode only)
- JSON mode: response_format {"type": "json_object"}; valid JSON only, no schema enforcement
- images: OpenAI-style image_url data URLs, user messages only
"""

import json
import time
from typing import Any

import httpx

from returns_agent.llm.client import LLMRequest, LLMResponse, ToolCall

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class DeepSeekProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_s: float = 60.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for the DeepSeek provider")
        self._model = model
        self._max_retries = max_retries
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = self._build_body(request)
        data = self._post_with_retries(body, request.timeout_s)
        message = data["choices"][0]["message"]
        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"]["arguments"] or "{}"),
            )
            for tc in message.get("tool_calls") or []
        ]
        return LLMResponse(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            model=data.get("model", self._model),
            usage=data.get("usage") or {},
        )

    def _build_body(self, request: LLMRequest) -> dict[str, Any]:
        messages = [dict(m) for m in request.messages]
        if request.images:
            last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
            text = messages[last_user]["content"]
            messages[last_user]["content"] = [
                {"type": "text", "text": text},
                *(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{img.media_type};base64,{img.data_b64}"},
                    }
                    for img in request.images
                ),
            ]
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "thinking": {"type": "enabled" if request.thinking else "disabled"},
        }
        if request.thinking and request.reasoning_effort:
            body["reasoning_effort"] = request.reasoning_effort
        if request.json_output:
            body["response_format"] = {"type": "json_object"}
        if request.tools:
            body["tools"] = request.tools
        return body

    def _post_with_retries(self, body: dict[str, Any], timeout_s: float | None) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.post("/chat/completions", json=body, timeout=timeout_s)
            except httpx.TransportError:
                if attempt == self._max_retries:
                    raise
            else:
                if resp.status_code not in RETRYABLE_STATUS or attempt == self._max_retries:
                    resp.raise_for_status()
                    result: dict[str, Any] = resp.json()
                    return result
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")
