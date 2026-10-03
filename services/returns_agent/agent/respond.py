"""Writes the customer-facing message for where the case now stands.

The LLM writes from a DECISION view that contains only what the system decided. Every
draft goes through the verifier; one regeneration is allowed, after which a deterministic
template built from the same decision is sent instead (always safe, never blocks the case).
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from returns_agent.agent.context import (
    conversation_messages,
    prompt_refs,
    redactor_for,
    system_message,
)
from returns_agent.agent.verifier import verify
from returns_agent.llm.client import LLMClient, LLMRequest, complete_structured
from returns_agent.llm.prompts import Prompt, load_prompt
from returns_agent.llm.redaction import Redactor

OPTION_LABELS = {
    "exchange": "an exchange",
    "replacement": "a replacement",
    "store_credit": "store credit",
    "refund": "a refund",
    "keep_item_refund": "a refund without returning the item",
}
SLOT_QUESTIONS = {"reason_category": "why you'd like to return the item"}
SITUATIONS = {
    "CLARIFY": "clarify",
    "REQUEST_EVIDENCE": "evidence",
    "CUSTOMER_CONFIRM": "offer",
    "HUMAN_APPROVAL": "under_review",
    "ESCALATE": "escalated",
    "TRACK_SHIPMENT": "pickup",
    "INSPECT_QC": "inspecting",
    "DISPUTE": "escalated",
}


class Draft(BaseModel):
    message: str


@dataclass
class Reply:
    text: str
    used_template: bool
    violations: list[str] = field(default_factory=list)
    prompt_refs: list[str] = field(default_factory=list)


def decision_view(facts: dict[str, Any], waiting_node: str | None) -> dict[str, Any]:
    """Only decided facts — the LLM cannot mention anything outside this."""
    quote = facts.get("refund_quote") or {}
    policy = facts.get("policy") or {}
    options = facts.get("options") or []
    money = any(o in options for o in ("refund", "store_credit", "keep_item_refund"))
    situation = SITUATIONS.get(waiting_node or "", "closed")
    view: dict[str, Any] = {
        "situation": situation,
        "language": facts.get("language", "en"),
        "options": options if situation == "offer" else [],
        "recommended": facts.get("chosen_option") if situation == "offer" else None,
        "amounts_minor": [quote["total_minor"]] if quote and money else [],
        "timeline_days": [],
        "missing_details": facts.get("missing_slots") or [],
        "evidence_needed": (facts.get("evidence") or {}).get("missing_views")
        or policy.get("required_evidence", []),
    }
    if situation == "closed":
        view["outcome"] = facts.get("close_outcome")
        view["declined_because"] = [
            t["clause_id"] for t in policy.get("trace", []) if t.get("result") == "failed"
        ]
        view["clause_texts"] = facts.get("explanation_texts", [])
        view["alternatives"] = (
            ["warranty claim", "human review"] if view["outcome"] == ("rejected") else []
        )
    return view


def template(view: dict[str, Any]) -> str:
    s = view["situation"]
    if s == "clarify":
        asks = [SLOT_QUESTIONS.get(d, d.replace("_", " ")) for d in view["missing_details"]]
        return f"Could you tell me {' and '.join(asks)}?"
    if s == "evidence":
        needed = [v for v in view["evidence_needed"] if v != "photo"]
        if needed:  # a targeted re-request: exactly the views the assessment is missing
            return f"Thanks for the photos. Could you also upload: {'; '.join(needed)}?"
        return "Please upload a photo of the item that shows the problem."
    if s == "offer":
        choices = " or ".join(OPTION_LABELS.get(o, o) for o in view["options"])
        amount = (
            f" The refund amount would be ₹{view['amounts_minor'][0] / 100:,.2f}."
            if view["amounts_minor"]
            else ""
        )
        return f"You can choose {choices}.{amount} Please confirm which you prefer."
    if s == "under_review":
        return "Thanks — your request is being reviewed by our team. We'll update you here."
    if s == "escalated":
        return "A specialist will look at your case and get back to you here."
    if s == "pickup":
        return "Your pickup is booked. We'll update you as the return moves."
    if s == "inspecting":
        return "We've received your item and are checking it now."
    outcome = view.get("outcome")
    if outcome == "resolved":
        return "Your return is complete. Thank you for your patience."
    if outcome == "rejected":
        texts = " ".join(view.get("clause_texts") or [])
        return (
            f"We're unable to accept this return. {texts} "
            "You can ask for a human review if you think this is wrong."
        ).replace("  ", " ")
    return "Your request has been closed. Reach out any time if you need help."


def reply(llm: LLMClient | None, facts: dict[str, Any], waiting_node: str | None) -> Reply:
    view = decision_view(facts, waiting_node)
    if llm is None:
        return Reply(text=template(view), used_template=True)
    prompt = load_prompt("respond")
    redactor = redactor_for(facts)
    history = conversation_messages(facts.get("conversation") or [], redactor)
    try:
        return _generate(llm, view, prompt, redactor, history)
    except Exception as exc:  # model down or unusable output: the safe template still goes out
        return Reply(
            text=template(view), used_template=True, violations=[f"llm_error: {type(exc).__name__}"]
        )


def _generate(
    llm: LLMClient,
    view: dict[str, Any],
    prompt: Prompt,
    redactor: Redactor,
    history: list[dict[str, str]],
) -> Reply:
    feedback: list[str] = []
    violations: list[str] = []
    for _ in range(2):
        data: dict[str, Any] = {"DECISION": view}
        if feedback:
            data["FIX_THESE_PROBLEMS"] = feedback
        request = LLMRequest(
            messages=[
                system_message(prompt, data),
                *history,
                {"role": "user", "content": "(Write the reply now as json.)"},
            ],
            max_tokens=512,
        )
        draft = complete_structured(llm, request, Draft).message
        violations = verify(llm, draft, view)
        if not violations:
            return Reply(
                text=redactor.restore(draft),
                used_template=False,
                prompt_refs=prompt_refs(prompt, load_prompt("verify")),
            )
        feedback = violations
    return Reply(
        text=template(view),
        used_template=True,
        violations=violations,
        prompt_refs=prompt_refs(prompt, load_prompt("verify")),
    )
