"""Wraps any LLMClient with usage metering and a circuit breaker."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from returns_agent.llm.client import LLMClient, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """The model is failing repeatedly; no decisions are made until it recovers."""


@dataclass
class CallRecord:
    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    ok: bool


@dataclass
class MeteredClient:
    inner: LLMClient
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    records: list[CallRecord] = field(default_factory=list)
    _consecutive_failures: int = 0
    _open_until: float = 0.0

    def complete(self, request: LLMRequest) -> LLMResponse:
        now = self.clock()
        if now < self._open_until:
            raise LLMUnavailable("circuit open: model recently failing")
        start = self.clock()
        try:
            response = self.inner.complete(request)
        except Exception:
            self._consecutive_failures += 1
            self.records.append(CallRecord("", (self.clock() - start) * 1000, 0, 0, ok=False))
            if self._consecutive_failures >= self.failure_threshold:
                self._open_until = self.clock() + self.cooldown_s
                logger.error("LLM circuit opened after %d failures", self._consecutive_failures)
            raise
        self._consecutive_failures = 0
        usage = response.usage
        self.records.append(
            CallRecord(
                model=response.model,
                latency_ms=(self.clock() - start) * 1000,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                ok=True,
            )
        )
        return response
