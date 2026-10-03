"""Node handlers for graph returns-v1.

Real: AUTHENTICATE, IDENTIFY_ORDER and UNDERSTAND_REQUEST (LLM when configured),
CHECK_ELIGIBILITY (policy engine), EXPLAIN_INELIGIBLE (clause texts), GENERATE_OPTIONS
(look-ahead + refund quote), RISK_SCORE / SCORE_OPTIONS / AUTONOMY_GATE (decision layer),
and the waiting nodes' event handling. Marked STUB nodes get their real logic later
(execution: Phase 6, evidence: Phase 8);
each stub keeps the state contract those phases fill in. Without an LLM, the LLM nodes
fall back to structured input from the API.
Handlers never call external systems directly; side effects go through the outbox (Phase 6).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from returns_agent.agent.identify import identify_item
from returns_agent.agent.understand import MIN_AGREEMENT, understand
from returns_agent.config import config_dir
from returns_agent.decision.config import DecisionConfig, load_decision_config
from returns_agent.decision.gate import confidence, decide_route
from returns_agent.decision.record import build_record
from returns_agent.decision.risk import RiskResult, assess_risk, item_value_minor
from returns_agent.decision.scoring import ScoredOption, keep_item_economical
from returns_agent.decision.scoring import score_options as rank_options
from returns_agent.graph.compiler import CaseState, Handler
from returns_agent.graph.lookahead import Feasibility, feasible_options
from returns_agent.graph.schema import GraphSpec, NodeSpec
from returns_agent.llm.client import LLMClient
from returns_agent.policy.engine import PolicyFacts, evaluate_policy
from returns_agent.policy.refund import Payment, RefundLine, compute_refund
from returns_agent.policy.schema import PolicyDoc, load_policies, select_version

REQUIRED_SLOTS = ("reason_category",)
OPTION_ORDER = ["exchange", "replacement", "store_credit", "refund", "keep_item_refund"]
MONEY_OPTIONS = {"refund", "store_credit", "keep_item_refund"}


@lru_cache
def _policies() -> tuple[list[PolicyDoc], list[PolicyDoc]]:
    return load_policies(config_dir() / "policies")


@lru_cache
def _clause_texts() -> dict[str, str]:
    legal, merchant = _policies()
    return {r.clause_id: r.text for doc in legal + merchant for r in doc.rules}


def _facts(state: CaseState) -> dict[str, Any]:
    return state.get("facts") or {}


def _event(state: CaseState) -> dict[str, Any]:
    return state.get("last_event") or {}


def _trace(f: dict[str, Any], node: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {**(f.get("llm_trace") or {}), node: entry}


@lru_cache
def default_decision_config() -> DecisionConfig:
    return load_decision_config(config_dir() / "decision.yaml")


def build_handlers(
    spec: GraphSpec,
    feasibility: Feasibility,
    llm: LLMClient | None = None,
    decision: Callable[[], DecisionConfig] = default_decision_config,
) -> dict[str, Handler]:
    def start(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"counters": {"turns": 1, "clarifications": 0, "evidence_requests": 0}}

    def authenticate(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        ok = bool(f.get("customer_id")) and f.get("principal_customer_id") == f.get("customer_id")
        return {"facts": {"authenticated": ok}}

    def identify_order(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        if f.get("order") or llm is None or not f.get("candidates"):
            return {}  # the API supplied the order/item, or there is nothing to match
        item_id, refs = identify_item(llm, f)
        update: dict[str, Any] = {"llm_trace": _trace(f, node.id, {"prompt_refs": refs})}
        if item_id is not None:
            update |= (f.get("candidate_facts") or {}).get(item_id, {})
        return {"facts": update}

    def understand_request(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        request = dict(f.get("request") or {})
        update: dict[str, Any] = {}
        if llm is not None and f.get("conversation"):
            result = understand(llm, f)
            ex = result.extraction
            confident = result.agreement >= MIN_AGREEMENT
            for key in ("reason_category", "desired_resolution"):
                value = getattr(ex, key)
                if value and confident and not request.get(key):
                    request[key] = value
            sku = str((f.get("item") or {}).get("sku", ""))
            if ex.exchange_variant and "-" in sku:
                request["exchange_sku"] = f"{sku.rsplit('-', 1)[0]}-{ex.exchange_variant.upper()}"
            update |= {
                "language": ex.language,
                "flags": {
                    "sentiment": ex.sentiment,
                    "wants_human": ex.wants_human,
                    "legal_threat": ex.legal_threat,
                },
                "llm_trace": _trace(
                    f, node.id, {"prompt_refs": result.prompt_refs, "agreement": result.agreement}
                ),
            }
        missing = [s for s in REQUIRED_SLOTS if not request.get(s)]
        prompt = f"Could you tell me more about: {', '.join(missing)}?" if missing else None
        return {
            "facts": {
                **update,
                "request": request,
                "missing_slots": missing,
                "pending_prompt": prompt,
            }
        }

    def clarify(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        e = _event(state)
        f = _facts(state)
        request = {**(f.get("request") or {}), **(e.get("answers") or {})}
        conversation = list(f.get("conversation") or [])
        if e.get("text"):
            conversation.append({"role": "customer", "text": e["text"]})
        counters = state.get("counters") or {}
        return {
            "facts": {"request": request, "pending_prompt": None, "conversation": conversation},
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
        # Structured explanation; the responder words it for the customer.
        policy = _facts(state).get("policy") or {}
        failed = [t["clause_id"] for t in policy.get("trace", []) if t["result"] == "failed"]
        texts = _clause_texts()
        return {
            "facts": {
                "explanation": policy.get("reason_codes", []),
                "explanation_texts": [texts[c] for c in failed if c in texts],
                "close_outcome": "rejected",
            }
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
        # Rule-based signals now; evidence signals and a model arrive in Phase 8.
        return {"facts": {"risk": assess_risk(_facts(state), decision()).model_dump()}}

    def generate_options(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        policy = f.get("policy") or {}
        allowed = [o for o in OPTION_ORDER if o in policy.get("allowed_resolutions", [])]
        risk = float((f.get("risk") or {}).get("score", 0.0))
        if "refund" in allowed and keep_item_economical(f, decision(), risk):
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
        f = _facts(state)
        risk = float((f.get("risk") or {}).get("score", 0.0))
        scored = rank_options(f.get("options") or [], f, decision(), risk)
        return {
            "facts": {
                "chosen_option": scored[0].option,
                "scored_options": [s.model_dump() for s in scored],
            }
        }

    def autonomy_gate(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        f = _facts(state)
        cfg = decision()
        risk = RiskResult.model_validate(f.get("risk") or {"score": 0.0, "signals": []})
        value = item_value_minor(f)
        conf = confidence(f)
        gate = decide_route(risk.score, value, conf["overall"], f.get("flags") or {}, cfg)
        if f.get("route_override") and gate.route != "escalate":
            # Test hook only: the API never sets route_override.
            gate = gate.model_copy(update={"route": f["route_override"], "reason": "override"})
        scored = [ScoredOption.model_validate(s) for s in f.get("scored_options") or []]
        record = build_record(f, scored, risk, conf, gate, value, cfg, state.get("graph_version"))
        return {"facts": {"route": gate.route, "decision": record}}

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
