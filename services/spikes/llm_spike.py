"""Phase 0 LLM spike — confirms DeepSeek-V4.1-Flash supports what the design needs.

Checks: basic chat, JSON output (validated), tool calling, image input, thinking mode.

Usage:
  DEEPSEEK_API_KEY=... uv run python spikes/llm_spike.py

Writes docs/spike-reports/llm.md.
"""

import base64
import struct
import sys
import time
import zlib
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from returns_agent.config import get_settings
from returns_agent.llm import ImageInput, LLMRequest, complete_structured
from returns_agent.llm.deepseek import DeepSeekProvider

REPORT = Path(__file__).resolve().parents[2] / "docs" / "spike-reports" / "llm.md"


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> str:
    """Build a solid-colour PNG without extra dependencies; returns base64."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * size
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


class Extraction(BaseModel):
    item: str
    reason_category: str
    desired_outcome: str


class Colour(BaseModel):
    colour: str


def main() -> None:
    settings = get_settings()  # reads env vars and services/.env
    provider = DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        model=settings.llm_model,
        base_url=settings.deepseek_base_url,
    )
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str]) -> None:
        start = time.monotonic()
        try:
            detail, ok = fn(), True
        except Exception as exc:  # report every failure, keep going
            detail, ok = f"{type(exc).__name__}: {exc}", False
        detail += f" ({time.monotonic() - start:.1f}s)"
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    def basic() -> str:
        r = provider.complete(LLMRequest(messages=[{"role": "user", "content": "Reply: OK"}]))
        assert r.text.strip(), "empty reply"
        return f"model={r.model!r}, usage={r.usage}"

    def json_output() -> str:
        msg = (
            "Extract item, reason_category (size_fit|damaged|wrong_item|not_as_described|"
            "other) and desired_outcome (refund|exchange|replacement) as JSON. "
            "Customer: 'The blue kurta I got is too tight, can I get L instead?'"
        )
        e = complete_structured(
            provider,
            LLMRequest(
                messages=[
                    {"role": "system", "content": "Return only JSON."},
                    {"role": "user", "content": msg},
                ]
            ),
            Extraction,
        )
        return e.model_dump_json()

    def tools() -> str:
        tool = {
            "type": "function",
            "function": {
                "name": "get_order",
                "description": "Fetch an order by ID",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
            },
        }
        r = provider.complete(
            LLMRequest(
                messages=[{"role": "user", "content": "What's the status of order ORD-1042?"}],
                tools=[tool],
            )
        )
        assert r.tool_calls and r.tool_calls[0].name == "get_order", f"no tool call: {r.text!r}"
        return f"called {r.tool_calls[0].name}({r.tool_calls[0].arguments})"

    def vision() -> str:
        c = complete_structured(
            provider,
            LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": 'What colour is this image? JSON: {"colour": "..."}',
                    }
                ],
                images=[ImageInput("image/png", solid_png((220, 20, 20)))],
            ),
            Colour,
        )
        assert "red" in c.colour.lower(), f"unexpected colour {c.colour!r}"
        return f"colour={c.colour!r}"

    def thinking() -> str:
        r = provider.complete(
            LLMRequest(
                messages=[{"role": "user", "content": "Is 17 a prime number? Answer yes or no."}],
                thinking=True,
                reasoning_effort="low",
            )
        )
        assert r.text.strip(), "empty reply"
        return f"reply={r.text.strip()[:40]!r}, usage={r.usage}"

    for name, fn in [
        ("Basic chat", basic),
        ("JSON output + schema validation", json_output),
        ("Tool calling", tools),
        ("Image input", vision),
        ("Thinking mode + reasoning effort", thinking),
    ]:
        check(name, fn)

    verdict = "GO" if all(ok for _, ok, _ in results) else "NO-GO — see failures"
    REPORT.write_text(
        "\n".join(
            [
                "# LLM spike report (DeepSeek-V4.1-Flash)",
                "",
                "| Check | Result | Detail |",
                "|---|---|---|",
                *(f"| {n} | {'PASS' if ok else 'FAIL'} | {d} |" for n, ok, d in results),
                "",
                "Also confirm manually from https://api-docs.deepseek.com/ and record here:",
                "- [ ] model ID `deepseek-flash` confirmed in the response above",
                "- [ ] rate limits and pricing",
                "- [ ] data retention terms and hosting region",
                "- [ ] hosting decision (official API / third-party / self-hosted)",
                "",
                f"**Verdict:** {verdict}",
            ]
        )
        + "\n"
    )
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
