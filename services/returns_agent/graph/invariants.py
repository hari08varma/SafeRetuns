"""Hard rules checked in code before entering a node — independent of the graph JSON,
so no graph edit (manual or self-evolved) can bypass them.

INV-6 (no commitments beyond the decision record) is enforced on outbound messages by
the verifier in the LLM layer, not on transitions.
"""

from typing import Any

PRE_AUTH_ALLOWED = frozenset({"START", "AUTHENTICATE", "ESCALATE", "CLOSE"})
NEEDS_ELIGIBILITY = frozenset(
    {
        "GENERATE_OPTIONS",
        "SCORE_OPTIONS",
        "AUTONOMY_GATE",
        "HUMAN_APPROVAL",
        "CUSTOMER_CONFIRM",
        "SCHEDULE_PICKUP",
        "TRACK_SHIPMENT",
        "INSPECT_QC",
        "ISSUE_REFUND",
        "CREATE_REPLACEMENT",
        "CREATE_EXCHANGE",
        "KEEP_ITEM_REFUND",
    }
)
REFUND_NODES = frozenset({"ISSUE_REFUND", "KEEP_ITEM_REFUND"})

LOOP_LIMITS = {"CLARIFY": ("clarifications", 3), "REQUEST_EVIDENCE": ("evidence_requests", 2)}
MAX_TURNS = 30


def check_invariants(target: str, state: dict[str, Any]) -> list[str]:
    """Returns the ids of invariants that entering `target` would break."""
    facts: dict[str, Any] = state.get("facts") or {}
    broken: list[str] = []
    if target not in PRE_AUTH_ALLOWED and facts.get("authenticated") is not True:
        broken.append("INV-1")  # no order data or PII before authentication
    if target in NEEDS_ELIGIBILITY and (facts.get("policy") or {}).get("eligible") is not True:
        broken.append("INV-2")  # no offer or execution before eligibility passes
    if target in REFUND_NODES:
        approval = facts.get("approval") or {}
        if facts.get("route") == "approval" and not (
            approval.get("status") == "approved" and approval.get("token")
        ):
            broken.append("INV-3")  # approval required and not granted
        quote = facts.get("refund_quote") or {}
        if not quote or quote.get("total_minor", 0) > quote.get("max_refundable_minor", -1):
            broken.append("INV-4")  # refund must be computed and within what was paid
        if facts.get("refund_issued"):
            broken.append("INV-5")  # one refund per return
    if (
        target == "CLOSE"
        and facts.get("close_outcome") == "resolved"
        and not facts.get("execution_succeeded")
    ):
        broken.append("INV-7")  # cannot close as resolved without a successful execution
    return broken


def loop_guard_exceeded(target: str, state: dict[str, Any]) -> str | None:
    counters: dict[str, int] = state.get("counters") or {}
    if counters.get("turns", 0) > MAX_TURNS:
        return "turns"
    limit = LOOP_LIMITS.get(target)
    if limit and counters.get(limit[0], 0) >= limit[1]:
        return limit[0]
    return None
