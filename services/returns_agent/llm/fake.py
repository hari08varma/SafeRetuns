"""Deterministic LLM provider for tests and local development."""

from returns_agent.llm.client import LLMRequest, LLMResponse


class FakeProvider:
    """Returns scripted responses in order and records every request."""

    def __init__(self, responses: list[str | LLMResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[LLMRequest] = []

    def add(self, response: str | LLMResponse) -> None:
        self._responses.append(response)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if not self._responses:
            raise RuntimeError("FakeProvider has no scripted responses left")
        item = self._responses.pop(0)
        return item if isinstance(item, LLMResponse) else LLMResponse(text=item, model="fake")
