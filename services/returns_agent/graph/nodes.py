"""Node handlers for graph returns-v1.

Real now: AUTHENTICATE, CHECK_ELIGIBILITY (policy engine), GENERATE_OPTIONS (look-ahead +
refund quote), and the waiting nodes' event handling. Marked STUB nodes get their real
logic in later phases (LLM: Phase 4, decisions: Phase 5, execution: Phase 6, evidence/
risk: Phase 8); each stub keeps the state contract those phases will fill in.
Handlers never call external systems directly; side effects go through the outbox (Phase 6).
"""

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from returns_agent.config import config_dir
from returns_agent.graph.compiler import CaseState, Handler
from returns_agent.graph.lookahead import Feasibility, feasible_options
from returns_agent.graph.schema import GraphSpec, NodeSpec
from returns_agent.policy.engine import PolicyFacts, evaluate_policy
from returns_agent.policy.refund import Payment, RefundLine, compute_refund
from returns_agent.policy.schema import PolicyDoc, load_policies, select_version

REQUIRED_SLOTS = ("reason_category",)
OPTION_ORDER = ["exchange", "replacement", "store_credit", "refund", "keep_item_refund"]
MONEY_OPTIONS = {"refund", "store_credit", "keep_item_refund"}


@lru_cache
def _policies() -> tuple[list[PolicyDoc], list[PolicyDoc]]:
    return load_policies(config_dir() / "policies")


def _facts(state: CaseState) -> dict[str, Any]:
    return state.get("facts") or {}


def _event(state: CaseState) -> dict[str, Any]:
    return state.get("last_event") or {}


def build_handlers(spec: GraphSpec, feasibility: Feasibility) -> dict[str, Handler]:
    def start(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"counters": {"turns": 1, "clarifications": 0, "evidence_requests": 0}}

    def authenticate(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        ok = bool(f.get("customer_id")) and f.get("principal_customer_id") == f.get("customer_id")
        return {"facts": {"authenticated": ok}}

    def identify_order(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {}  # STUB (Phase 4): match free text to order/item; API supplies them for now

    def understand_request(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        # STUB (Phase 4): LLM extraction. Detects missing slots from structured input.
        request = _facts(state).get("request") or {}
        missing = [s for s in REQUIRED_SLOTS if not request.get(s)]
        prompt = f"Could you tell me more about: {', '.join(missing)}?" if missing else None
        return {"facts": {"missing_slots": missing, "pending_prompt": prompt}}

    def clarify(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        answers = _event(state).get("answers") or {}
        request = {**(_facts(state).get("request") or {}), **answers}
        counters = state.get("counters") or {}
        return {
            "facts": {"request": request, "pending_prompt": None},
            "counters": {
                "clarifications": counters.get("clarifications", 0) + 1,
                "turns": counters.get("turns", 0) + 1,
            },
        }

    def check_eligibility(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        facts = PolicyFacts.model_validate(
            {"order": f["order"], "item": f["item"], "request": f["request"]}
        )
        legal_docs, merchant_docs = _policies()
        now = datetime.now(UTC)
        decision = evaluate_policy(
            select_version(legal_docs, now),
            select_version(merchant_docs, facts.order.placed_at),
            facts,
            now,
        )
        update: dict[str, Any] = {"policy": decision.model_dump(mode="json")}
        if not decision.eligible:
            update["close_outcome"] = "rejected"
        return {"facts": update}

    def explain_ineligible(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        # STUB (Phase 4): LLM wording. Deterministic explanation from the policy trace.
        policy = _facts(state).get("policy") or {}
        return {
            "facts": {"explanation": policy.get("reason_codes", []), "close_outcome": "rejected"}
        }

    def request_evidence(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        counters = state.get("counters") or {}
        return {
            "facts": {"evidence_provided": True, "evidence_files": _event(state).get("files", [])},
            "counters": {
                "evidence_requests": counters.get("evidence_requests", 0) + 1,
                "turns": counters.get("turns", 0) + 1,
            },
        }

    def assess_evidence(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"evidence": {"needs_more": False}}}  # STUB (Phase 8)

    def risk_score(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        # STUB (Phase 5/8): keeps any risk supplied upstream, else zero.
        return {"facts": {"risk": _facts(state).get("risk") or {"score": 0.0, "signals": []}}}

    def generate_options(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        policy = f.get("policy") or {}
        allowed = [o for o in OPTION_ORDER if o in policy.get("allowed_resolutions", [])]
        if f.get("keep_item_eligible") and "refund" in allowed:
            allowed.append("keep_item_refund")
        quote = _refund_quote(f, policy)
        pruned: dict[str, str] = {}
        if quote is None:
            pruned |= {o: "no refund quote" for o in allowed if o in MONEY_OPTIONS}
            allowed = [o for o in allowed if o not in MONEY_OPTIONS]
        feasible, infeasible = feasible_options(spec, allowed, dict(state), feasibility)
        return {
            "facts": {
                "options": feasible,
                "pruned_options": pruned | infeasible,
                "refund_quote": quote,
            }
        }

    def score_options(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        # STUB (Phase 5): utility scoring. Customer preference if offered, else default order.
        f = _facts(state)
        options = f.get("options") or []
        preferred = (f.get("request") or {}).get("desired_resolution")
        return {"facts": {"chosen_option": preferred if preferred in options else options[0]}}

    def autonomy_gate(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"route": _facts(state).get("route_override") or "auto"}}  # STUB (5)

    def human_approval(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        e = _event(state)
        decision = e.get("decision")
        status = {"approve": "approved", "modify": "approved", "reject": "rejected"}.get(
            str(decision)
        )
        if status is None:
            raise ValueError(f"unknown approval decision {decision!r}")
        update: dict[str, Any] = {
            "approval": {
                "status": status,
                "approver_id": e.get("approver_id"),
                "token": e.get("token"),
                "reason_code": e.get("reason_code"),
            }
        }
        if decision == "modify":
            option = e.get("option")
            if option not in (_facts(state).get("options") or []):
                raise ValueError(f"approver chose an option that was not offered: {option!r}")
            update["chosen_option"] = option
        if status == "rejected":
            update["close_outcome"] = "rejected"
        return {"facts": update}

    def customer_confirm(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        e = _event(state)
        counters = state.get("counters") or {}
        update: dict[str, Any] = {"confirmed": bool(e.get("accept"))}
        option = e.get("option")
        if option is not None:
            if option not in (_facts(state).get("options") or []):
                raise ValueError(f"customer chose an option that was not offered: {option!r}")
            update["chosen_option"] = option
        if not update["confirmed"]:
            update["close_outcome"] = "cancelled"
        return {"facts": update, "counters": {"turns": counters.get("turns", 0) + 1}}

    # --- Execution: STUB (Phase 6) — records intent; real side effects via the outbox ----

    def create_exchange(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"exchange": {"status": "requested"}}}

    def create_replacement(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"replacement": {"status": "requested"}}}

    def schedule_pickup(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"pickup": {"scheduled": True}}}

    def track_shipment(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        e = _event(state)
        shipment = dict(
            _facts(state).get("shipment") or {"status": "pending", "failed_attempts": 0}
        )
        kind = e.get("event")
        if kind == "pickup_failed":
            shipment["failed_attempts"] = shipment.get("failed_attempts", 0) + 1
        elif kind in ("picked_up", "in_transit", "received"):
            shipment["status"] = kind
        else:
            raise ValueError(f"unknown carrier event {kind!r}")
        return {"facts": {"shipment": shipment}}

    def inspect_qc(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        e = _event(state)
        passed = bool(e.get("passed"))
        update: dict[str, Any] = {"qc": {"passed": passed, "grade": e.get("grade")}}
        if passed and _facts(state).get("chosen_option") in ("exchange", "replacement"):
            update |= {"execution_succeeded": True, "close_outcome": "resolved"}
        return {"facts": update}

    def issue_refund(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {
            "facts": {
                "refund_issued": True,
                "execution_succeeded": True,
                "close_outcome": "resolved",
            }
        }

    def human_resolution(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        e = _event(state)
        outcome = e.get("outcome")
        if outcome not in ("resolved_by_human", "rejected", "cancelled"):
            raise ValueError(f"unknown resolution outcome {outcome!r}")
        return {"facts": {"close_outcome": outcome, "resolved_by": e.get("staff_id")}}

    def close(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"closed_at": datetime.now(UTC).isoformat()}}

    return {
        "START": start,
        "AUTHENTICATE": authenticate,
        "IDENTIFY_ORDER": identify_order,
        "UNDERSTAND_REQUEST": understand_request,
        "CLARIFY": clarify,
        "CHECK_ELIGIBILITY": check_eligibility,
        "EXPLAIN_INELIGIBLE": explain_ineligible,
        "REQUEST_EVIDENCE": request_evidence,
        "ASSESS_EVIDENCE": assess_evidence,
        "RISK_SCORE": risk_score,
        "GENERATE_OPTIONS": generate_options,
        "SCORE_OPTIONS": score_options,
        "AUTONOMY_GATE": autonomy_gate,
        "HUMAN_APPROVAL": human_approval,
        "CUSTOMER_CONFIRM": customer_confirm,
        "CREATE_EXCHANGE": create_exchange,
        "CREATE_REPLACEMENT": create_replacement,
        "SCHEDULE_PICKUP": schedule_pickup,
        "TRACK_SHIPMENT": track_shipment,
        "INSPECT_QC": inspect_qc,
        "ISSUE_REFUND": issue_refund,
        "KEEP_ITEM_REFUND": issue_refund,
        "DISPUTE": human_resolution,
        "ESCALATE": human_resolution,
        "CLOSE": close,
    }


def _refund_quote(f: dict[str, Any], policy: dict[str, Any]) -> dict[str, int] | None:
    pricing = f.get("pricing")
    item = f.get("item") or {}
    if not pricing or not item:
        return None
    breakdown = compute_refund(
        [
            RefundLine(
                unit_price_minor=pricing["unit_price_minor"],
                qty_ordered=item["qty_ordered"],
                line_discount_minor=pricing.get("line_discount_minor", 0),
                qty_returning=item["qty_returning"],
                qty_already_returned=item.get("qty_already_returned", 0),
            )
        ],
        [Payment.model_validate(p) for p in pricing.get("payments", [])],
        restocking_fee_pct=policy.get("restocking_fee_pct", 0),
        shipping_fee_minor=pricing.get("shipping_fee_minor", 0),
        shipping_refundable=policy.get("shipping_refundable", True),
        returns_whole_order=pricing.get("returns_whole_order", False),
    )
    return {
        "total_minor": breakdown.total_minor,
        "max_refundable_minor": breakdown.items_minor + breakdown.shipping_minor,
    }
