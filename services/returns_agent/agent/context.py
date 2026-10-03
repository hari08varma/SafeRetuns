"""Builds LLM requests with strict role separation: operator instructions and data live in
the system message; customer text appears only in user messages, always redacted."""

import json
from typing import Any

from returns_agent.llm.prompts import Prompt, load_prompt
from returns_agent.llm.redaction import Redactor


def redactor_for(facts: dict[str, Any]) -> Redactor:
    pii = facts.get("pii") or {}
    labels = {"name": "NAME", "phone": "PHONE", "email": "EMAIL", "address": "ADDRESS"}
    return Redactor(known={str(v): labels[k] for k, v in pii.items() if k in labels and v})


def system_message(
    task: Prompt, data: dict[str, Any] | None = None, guidance: dict[str, Any] | None = None
) -> dict[str, str]:
    parts = [load_prompt("system").text, task.text]
    if guidance:
        hints = [
            {"to": e["to"], "guidance": e["guidance"], "pitfalls": e["pitfalls"]}
            for e in guidance.get("edges", [])
            if e.get("guidance") or e.get("pitfalls")
        ]
        if hints:
            parts.append("WORKFLOW NOTES:\n" + json.dumps(hints, ensure_ascii=False))
    for key, value in (data or {}).items():
        parts.append(f"{key}:\n{json.dumps(value, ensure_ascii=False, default=str)}")
    return {"role": "system", "content": "\n\n".join(parts)}


def conversation_messages(
    conversation: list[dict[str, str]], redactor: Redactor
) -> list[dict[str, str]]:
    """Customer turns as `user`, agent turns as `assistant`; every turn redacted."""
    messages = []
    for turn in conversation:
        role = "user" if turn.get("role") == "customer" else "assistant"
        messages.append({"role": role, "content": redactor.redact(turn.get("text", ""))})
    return messages


def prompt_refs(*prompts: Prompt) -> list[str]:
    return [p.ref for p in (load_prompt("system"), *prompts)]
