"""Runs eval cases against the real system: customer API, graph runner, decision layer,
outbox relay with mock adapters, carrier webhooks and QC. Each trial gets its own
customer and order, then the end state is read back from the database and graded."""

import json
import secrets
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import count
from typing import Any

from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from returns_agent.adapters.bundle import Adapters
from returns_agent.adapters.mock import (
    FaultPlan,
    MockCarrierAdapter,
    MockInventoryAdapter,
    MockNotificationAdapter,
    MockOrderAdapter,
    MockPaymentAdapter,
)
from returns_agent.agent.cases import DEAD, CaseService
from returns_agent.api.app import create_app
from returns_agent.config import config_dir, get_settings
from returns_agent.db.models import (
    Address,
    AuditEvent,
    Customer,
    Evidence,
    Exchange,
    Message,
    Order,
    OrderItem,
    Outbox,
    Product,
    Refund,
    ReplacementOrder,
    ReturnCase,
    ReturnItem,
    Shipment,
)
from returns_agent.evals.cases import SCRIPTED, EvalCase
from returns_agent.evals.graders import (
    Observation,
    ToneScore,
    detect_violations,
    grade_clauses,
    grade_state,
    judge_tone,
)
from returns_agent.evals.photos import make_photo
from returns_agent.evals.simulator import Customer as SimCustomer
from returns_agent.evals.simulator import LLMCustomer, ScriptedCustomer, confirm_choice
from returns_agent.evidence.checks import IST
from returns_agent.evidence.hashing import orientation_hashes
from returns_agent.evidence.intake import sanitise
from returns_agent.execution.relay import Relay
from returns_agent.execution.webhooks import CarrierWebhook, handle_carrier
from returns_agent.graph.nodes import build_handlers
from returns_agent.graph.registry import GraphRegistry
from returns_agent.graph.runner import CaseRunner, make_feasibility
from returns_agent.llm.client import LLMClient
from returns_agent.llm.metering import MeteredClient
from returns_agent.security.pii import address_index, blind_index, encrypt
from returns_agent.security.tokens import Principal, Role, issue_token
from returns_agent.seed.generator import generate

MAX_STEPS = 40
PHOTO_KINDS = {"", "stale", "edited", "ai", "dup-other", "reuse-own", "catalogue"}
STOP_WAITS = ("approval", "human_resolution")  # handed to people: the agent's part is done


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


@dataclass
class TrialResult:
    case_id: str
    suite: str
    tags: list[str]
    persona: str
    trial: int
    passed: bool
    failures: list[str]
    violations: list[str]
    observation: Observation
    seconds: float
    agent_usage: Usage = field(default_factory=Usage)
    customer_usage: Usage = field(default_factory=Usage)
    tone: ToneScore | None = None


@dataclass
class _World:
    customer_id: uuid.UUID
    order_ref: str
    item_ref: str
    item_id: uuid.UUID
    pii: list[str]
    others_pii: list[str]
    title: str
    photos: list[tuple[str, bytes]]


class Harness:
    def __init__(
        self,
        engine: Engine,
        runner: CaseRunner,
        cases: CaseService,
        adapters: Adapters,
        agent_llm: LLMClient | None = None,
        customer_llm: LLMClient | None = None,
        judge_llm: LLMClient | None = None,
    ) -> None:
        self.runner = runner
        self.cases = cases
        self.adapters = adapters
        self.relay = Relay(engine, runner, adapters)
        self.client = TestClient(create_app(adapters, cases=cases))
        self.sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self.agent_llm = agent_llm
        self.customer_llm = customer_llm
        self.judge_llm = judge_llm
        self.run_token = secrets.token_hex(3)
        if isinstance(adapters.carrier, MockCarrierAdapter):
            adapters.carrier.awb_prefix = f"AWB{self.run_token.upper()}-"
        self._ids = count(1)
        self._stock = {p.sku: 20 for p in generate(seed=1, customers=1).products}
        system = (config_dir() / "prompts" / "system.md").read_text().splitlines()
        self._prompt_lines = [line.strip("- ").strip() for line in system if len(line) > 40]

    # --- One trial ---------------------------------------------------------------------------

    def run(self, case: EvalCase, persona: str, trial: int = 1) -> TrialResult:
        if persona != SCRIPTED and self.customer_llm is None:
            raise ValueError("LLM personas need a customer model")
        started = time.monotonic()
        agent_mark, customer_mark = _mark(self.agent_llm), _mark(self.customer_llm)
        self._reset_adapters(case)
        with self.sessions() as session:
            world = self._build_world(session, case)
        case_id: uuid.UUID | None = None
        error: str | None = None
        for _ in range(case.world.repeat):
            customer: SimCustomer = (
                ScriptedCustomer(case, {"phone": world.pii[0], "email": world.pii[1]})
                if persona == SCRIPTED
                else LLMCustomer(case, persona, self.customer_llm, world.title)  # type: ignore[arg-type]
            )
            try:
                case_id, error = self._drive(case, persona, world, customer)
            except Exception as exc:  # a crash is a failed trial, never a crashed run
                error = f"{type(exc).__name__}: {exc}"
            if error:
                break
        obs = self._observe(world, case_id, error)
        failures = grade_state(case, obs) + grade_clauses(case, obs)
        if obs.error:
            failures.insert(0, f"error: {obs.error}")
        violations = detect_violations(obs, self._prompt_lines)
        tone = None
        if self.judge_llm is not None:
            try:
                tone = judge_tone(self.judge_llm, obs)
            except Exception:
                tone = None  # tone is informational; never fails a trial
        return TrialResult(
            case_id=case.id,
            suite=case.suite,
            tags=case.tags,
            persona=persona,
            trial=trial,
            passed=not failures and not violations,
            failures=failures,
            violations=violations,
            observation=obs,
            seconds=round(time.monotonic() - started, 3),
            agent_usage=_usage_since(self.agent_llm, agent_mark),
            customer_usage=_usage_since(self.customer_llm, customer_mark),
            tone=tone,
        )

    def _drive(
        self, case: EvalCase, persona: str, world: _World, customer: SimCustomer
    ) -> tuple[uuid.UUID | None, str | None]:
        goal = case.goal
        headers = _auth(Principal(str(world.customer_id), Role.CUSTOMER))
        body: dict[str, Any] = {
            "order_id": world.order_ref,
            "item_id": world.item_ref,
            "qty": goal.qty_returning,
            "message": customer.opening(),
        }
        if persona == SCRIPTED and goal.ui_selections:
            body |= {
                "reason_category": goal.reason_category,
                "desired_resolution": goal.desired_resolution,
                "exchange_sku": goal.exchange_sku,
                "is_gift": goal.is_gift or None,
            }
        r = self.client.post("/api/v1/cases", json=body, headers=headers)
        if r.status_code == 404:
            return None, None
        if r.status_code != 201:
            return None, f"open case: HTTP {r.status_code} {r.text[:200]}"
        case_id = uuid.UUID(r.json()["case_id"])
        base = f"/api/v1/cases/{case_id}"
        carrier = list(case.world.carrier_events)
        turns = 1
        for _ in range(MAX_STEPS):
            status, node, waiting = self._state(case_id)
            if status == "closed" or waiting in STOP_WAITS:
                self._settle(case_id)  # queued side effects (compensation, notices) still run
                return case_id, None
            if waiting == "customer_message":
                if turns >= case.max_turns:
                    return case_id, f"no resolution within {case.max_turns} customer turns"
                text = customer.reply(self.client.get(f"{base}/messages", headers=headers).json())
                if text is None:  # the customer stops answering: the inactivity timer fires
                    self.runner.timeout(case_id, node, "customer_message")
                    continue
                r = self.client.post(f"{base}/messages", json={"text": text}, headers=headers)
                turns += 1
            elif waiting == "customer_upload":
                if not goal.evidence:
                    self.runner.timeout(case_id, node, "customer_upload")
                    continue
                files = [("files", (name, data, "image/jpeg")) for name, data in world.photos]
                r = self.client.post(f"{base}/evidence", files=files, headers=headers)
            elif waiting == "customer_confirm":
                choice = confirm_choice(case, self.runner.facts(case_id).get("options") or [])
                payload = choice or {"accept": False}
                r = self.client.post(f"{base}/confirm", json=payload, headers=headers)
            elif waiting == "action_result":
                if not self._drain(case_id):
                    return case_id, f"action at {node} never completed"
                continue
            elif waiting == "carrier_event":
                if not carrier:
                    return case_id, "waiting for a carrier event the scenario does not send"
                self._carrier(case_id, carrier.pop(0))
                continue
            elif waiting == "qc_result":
                r = self.client.post(
                    f"/api/v1/console/cases/{case_id}/qc",
                    json={"passed": case.world.qc_passed, "grade": "A"},
                    headers=_auth(Principal("eval-qc", Role.QC_OPERATOR)),
                )
            else:
                return case_id, f"unexpected wait {waiting!r} at {node}"
            if r.status_code >= 400:
                return case_id, f"{waiting}: HTTP {r.status_code} {r.text[:200]}"
        return case_id, f"no end state within {MAX_STEPS} steps"

    def _state(self, case_id: uuid.UUID) -> tuple[str, str, str | None]:
        with self.sessions() as session:
            case = session.get(ReturnCase, case_id)
            assert case is not None
            if case.status == "closed":
                return case.status, case.current_node, None
            spec = self.runner.registry.spec(case.graph_version)
            return case.status, case.current_node, spec.node(case.current_node).waits_for

    def _drain(self, case_id: uuid.UUID) -> bool:
        """Run the worker until the case's actions finish; True if the case moved on."""
        before = self._state(case_id)
        self._settle(case_id)
        return self._state(case_id) != before

    def _settle(self, case_id: uuid.UUID) -> None:
        """Run the worker until nothing is queued for the case, skipping back-off waits."""
        now = datetime.now(UTC)
        for _ in range(10):
            self.relay.drain(now)
            with self.sessions() as session:
                pending = session.scalar(
                    select(func.count())
                    .select_from(Outbox)
                    .where(Outbox.case_id == case_id, Outbox.status.in_(("pending", "retry")))
                )
            if not pending:
                break
            now += timedelta(hours=1)

    def _carrier(self, case_id: uuid.UUID, event: str) -> None:
        with self.sessions() as session:
            awb = session.scalars(select(Shipment.awb).where(Shipment.case_id == case_id)).first()
            hook = CarrierWebhook(event_id=uuid.uuid4().hex, awb=str(awb), event=event)  # type: ignore[arg-type]
            handle_carrier(session, self.runner, hook)

    # --- World set-up --------------------------------------------------------------------------

    def _reset_adapters(self, case: EvalCase) -> None:
        faults = FaultPlan(sleep=lambda _: None)
        for operation, outcomes in case.world.faults.items():
            faults.inject(operation, *outcomes)
        a = self.adapters
        for adapter in (a.orders, a.carrier, a.payment, a.inventory, a.notification):
            adapter.faults = faults  # type: ignore[union-attr]
        assert isinstance(a.inventory, MockInventoryAdapter)
        a.inventory.stock = {**self._stock, **dict.fromkeys(case.world.out_of_stock, 0)}

    def _new_customer(
        self, session: Session, case: EvalCase, pincode: str
    ) -> tuple[Customer, list[str]]:
        n = next(self._ids)
        tag = f"{self.run_token}-{n}"
        phone = f"9{int(self.run_token, 16) % 10_000:04d}{n % 100_000:05d}"
        email = f"eval.{tag}@example.com"
        customer = Customer(
            external_id=f"EVAL-{tag}",
            name_enc=encrypt(f"Eval Customer {n}"),
            phone_enc=encrypt(phone),
            phone_index=blind_index(phone),
            email_enc=encrypt(email),
            email_index=blind_index(email),
            account_age_days=case.customer.account_age_days,
        )
        session.add(customer)
        session.flush()
        session.add(
            Address(
                customer_id=customer.id,
                city="Hyderabad",
                pincode=pincode,
                address_enc=encrypt(f"Flat {n}, Hyderabad {pincode}"),
                address_index=address_index(f"Flat {n}, Hyderabad {pincode}"),
            )
        )
        return customer, [phone, email]

    def _build_world(self, session: Session, case: EvalCase) -> _World:
        o, now = case.order, datetime.now(UTC)
        pincode = "500081"
        if case.world.unserviceable:
            pincode = f"9{next(self._ids) % 100_000:05d}"
            assert isinstance(self.adapters.carrier, MockCarrierAdapter)
            self.adapters.carrier.unserviceable = {pincode}
        customer, pii = self._new_customer(session, case, pincode)
        owner, others = customer, list[str]()
        if case.world.foreign_order:
            owner, others = self._new_customer(session, case, "560034")
        product = session.scalars(select(Product).where(Product.sku == o.sku)).one()
        price = o.unit_price_minor if o.unit_price_minor is not None else product.price_minor
        delivered = (
            now - timedelta(days=o.delivered_days_ago)
            if o.status == "delivered" and o.delivered_days_ago is not None
            else None
        )
        ref = f"EV-{owner.external_id.removeprefix('EVAL-')}"
        order = Order(
            external_id=ref,
            customer_id=owner.id,
            placed_at=(delivered or now) - timedelta(days=3),
            delivered_at=delivered,
            status=o.status,
            payment_method=o.payment_method,
            coupon_minor=o.discount_minor,
            total_minor=price * o.qty - o.discount_minor,
        )
        item = OrderItem(
            external_id=f"{ref}-1",
            sku=o.sku,
            qty=o.qty,
            unit_price_minor=price,
            discount_alloc_minor=o.discount_minor,
            final_sale=o.final_sale,
        )
        order.items = [item]
        session.add(order)
        self._history(session, customer, case, now)
        photos = self._photos(session, case, customer, product, delivered or now)
        session.commit()
        return _World(
            customer.id, ref, item.external_id, item.id, pii, others, product.title, photos
        )

    def _photos(
        self,
        session: Session,
        case: EvalCase,
        customer: Customer,
        product: Product,
        delivered: datetime,
    ) -> list[tuple[str, bytes]]:
        """Evidence the customer will upload. A spec is "name" (a fresh photo taken after
        delivery) or "kind:name" for an authenticity test: stale (taken before delivery),
        edited (editor in metadata), ai (generator marker), dup-other (same picture already
        used by another customer), reuse-own (used in this customer's earlier case),
        catalogue (the shop's own product photo)."""
        photos = []
        for spec in case.goal.evidence:
            kind, _, name = spec.rpartition(":")
            if kind not in PHOTO_KINDS:
                raise ValueError(f"unknown evidence kind {kind!r} in {case.id}")
            taken = delivered + (timedelta(days=-10) if kind == "stale" else timedelta(hours=2))
            data = make_photo(
                f"{self.run_token}-{next(self._ids)}-{name}",
                captured_at=taken.astimezone(IST).replace(tzinfo=None),
                software="Adobe Photoshop 25.0" if kind == "edited" else None,
                comment="Generated with Midjourney v6" if kind == "ai" else None,
            )
            phash = orientation_hashes(sanitise(data).image)[0]
            if kind == "catalogue":
                product.image_phashes = [*product.image_phashes, phash]
            elif kind in ("dup-other", "reuse-own"):
                owner = customer
                if kind == "dup-other":
                    owner, _ = self._new_customer(session, case, "110017")
                self._plant_evidence(session, owner, phash)
            photos.append((name, data))
        return photos

    def _plant_evidence(self, session: Session, owner: Customer, phash: str) -> None:
        """An earlier, closed case of `owner` whose evidence has this perceptual hash."""
        now = datetime.now(UTC)
        order = Order(
            external_id=f"EVP-{owner.external_id}-{next(self._ids)}",
            customer_id=owner.id,
            placed_at=now - timedelta(days=60),
            delivered_at=now - timedelta(days=57),
            status="delivered",
            payment_method="upi",
            coupon_minor=0,
            total_minor=59900,
        )
        session.add(order)
        session.flush()
        earlier = ReturnCase(
            customer_id=owner.id,
            order_id=order.id,
            graph_version=self.runner.registry.active_version,
            status="closed",
            current_node="CLOSE",
            closed_at=now - timedelta(days=50),
        )
        session.add(earlier)
        session.flush()
        session.add(
            Evidence(case_id=earlier.id, uri="file://planted", mime="image/jpeg", phash=phash)
        )

    def _history(self, session: Session, customer: Customer, case: EvalCase, now: datetime) -> None:
        """Earlier orders and returns in the last 90 days (risk signals read these)."""
        c = case.customer
        plain_returns = c.prior_returns_90d
        extra_orders = max(0, c.prior_orders_90d - plain_returns - c.prior_damage_claims_90d)
        self._past_orders(session, customer, extra_orders, now)
        self._past_returns(session, customer, plain_returns, now)
        self._past_returns(session, customer, c.prior_damage_claims_90d, now, reason="defective")
        if c.confirmed_fraud:
            customer.risk_profile = {"confirmed_fraud": True}
        if c.linked_risky_account:
            linked, _ = self._new_customer(session, case, "500081")
            session.flush()
            mine = session.scalars(select(Address).where(Address.customer_id == customer.id)).one()
            theirs = session.scalars(select(Address).where(Address.customer_id == linked.id)).one()
            theirs.address_index = mine.address_index  # same delivery address
            self._past_returns(session, linked, 3, now)

    def _past_orders(
        self, session: Session, owner: Customer, count_: int, now: datetime
    ) -> list[OrderItem]:
        items = []
        for _ in range(count_):
            ref = f"EVH-{owner.external_id}-{next(self._ids)}"
            order = Order(
                external_id=ref,
                customer_id=owner.id,
                placed_at=now - timedelta(days=40),
                delivered_at=now - timedelta(days=36),
                status="delivered",
                payment_method="upi",
                coupon_minor=0,
                total_minor=59900,
            )
            item = OrderItem(
                external_id=f"{ref}-1",
                sku="TSH-M",
                qty=1,
                unit_price_minor=59900,
                discount_alloc_minor=0,
                final_sale=False,
            )
            order.items = [item]
            session.add(order)
            items.append(item)
        session.flush()
        return items

    def _past_returns(
        self,
        session: Session,
        owner: Customer,
        count_: int,
        now: datetime,
        reason: str | None = None,
    ) -> None:
        """Closed earlier returns, each on its own past order (counted as orders too)."""
        for item in self._past_orders(session, owner, count_, now):
            earlier = ReturnCase(
                customer_id=owner.id,
                order_id=item.order_id,
                graph_version=self.runner.registry.active_version,
                status="closed",
                current_node="CLOSE",
                closed_at=now - timedelta(days=30),
            )
            session.add(earlier)
            session.flush()
            session.add(
                ReturnItem(case_id=earlier.id, order_item_id=item.id, qty=1, reason_category=reason)
            )

    # --- Reading the end state -------------------------------------------------------------

    def _observe(self, world: _World, case_id: uuid.UUID | None, error: str | None) -> Observation:
        with self.sessions() as session:
            item = session.get(OrderItem, world.item_id)
            assert item is not None
            line_paid = item.unit_price_minor * item.qty - item.discount_alloc_minor
            line_refunded = session.scalar(
                select(func.coalesce(func.sum(Refund.amount_minor), 0))
                .join(ReturnItem, ReturnItem.case_id == Refund.case_id)
                .where(ReturnItem.order_item_id == item.id, Refund.status == "succeeded")
            )
            base = Observation(
                outcome="not_found" if error is None else "error",
                error=error,
                line_paid_minor=line_paid,
                line_refunded_minor=int(line_refunded or 0),
                own_pii=world.pii,
                others_pii=world.others_pii,
            )
            if case_id is None:
                return base
            case = session.get(ReturnCase, case_id)
            assert case is not None
            facts = self.runner.facts(case_id)
            audit = session.scalars(
                select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.seq)
            ).all()
            path = [str(a.payload.get("node")) for a in audit if a.action == "graph.node"]
            messages = session.scalars(
                select(Message).where(Message.case_id == case_id).order_by(Message.created_at)
            ).all()
            refunds = [
                (r.amount_minor, r.method)
                for r in session.scalars(select(Refund).where(Refund.case_id == case_id))
                if r.status == "succeeded"
            ]
            exchanges = _live(session, Exchange, case_id)
            replacements = _live(session, ReplacementOrder, case_id)

        base.status, base.current_node, base.route = case.status, case.current_node, case.route
        base.outcome = _outcome(case, facts) if error is None else "error"
        base.refunds = refunds
        base.refund_minor = sum(a for a, _ in refunds)
        base.exchanges, base.replacements = exchanges, replacements
        base.resolution = _resolution(path, facts, refunds, exchanges, replacements)
        base.approved_by_human = (
            "HUMAN_APPROVAL" in path and (facts.get("approval") or {}).get("status") == "approved"
        )
        base.policy = facts.get("policy") or {}
        base.quote = facts.get("refund_quote") or {}
        base.path = path
        base.transcript = [{"role": m.role, "text": m.content} for m in messages]
        base.redacted_messages = [m.redacted_content or "" for m in messages]
        base.audit_text = json.dumps([a.payload for a in audit], default=str)
        base.guardrail_hits = [
            str(a.payload.get("violation")) for a in audit if a.action == "graph.violation"
        ]
        base.template_fallbacks = sum(
            1 for a in audit if a.action == "message.sent" and a.payload.get("used_template")
        )
        return base


def _outcome(case: ReturnCase, facts: dict[str, Any]) -> str:
    if case.status == "closed":
        outcome = str(facts.get("close_outcome") or "resolved")
        return "resolved" if outcome == "resolved_by_human" else outcome
    if case.status == "escalated":
        return "escalated"
    if case.current_node == "HUMAN_APPROVAL":
        return "approval"
    if case.current_node == "DISPUTE":
        return "dispute"
    return f"stuck:{case.current_node}"


def _resolution(
    path: list[str],
    facts: dict[str, Any],
    refunds: list[tuple[int, str]],
    exchanges: int,
    replacements: int,
) -> str:
    if exchanges:
        return "exchange"
    if replacements:
        return "replacement"
    if refunds:
        if "KEEP_ITEM_REFUND" in path:
            return "keep_item_refund"
        return "store_credit" if facts.get("chosen_option") == "store_credit" else "refund"
    return "none"


def _live(
    session: Session, model: type[Exchange] | type[ReplacementOrder], case_id: uuid.UUID
) -> int:
    statuses = session.scalars(select(model.status).where(model.case_id == case_id)).all()
    return sum(1 for s in statuses if s not in DEAD)


def _auth(principal: Principal) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(principal, 'access')}"}


def _mark(llm: LLMClient | None) -> int:
    return len(llm.records) if isinstance(llm, MeteredClient) else 0


def _usage_since(llm: LLMClient | None, mark: int) -> Usage:
    if not isinstance(llm, MeteredClient):
        return Usage()
    records = llm.records[mark:]
    return Usage(
        calls=len(records),
        input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records),
        latency_ms=round(sum(r.latency_ms for r in records), 1),
    )


def mock_adapters() -> Adapters:
    faults = FaultPlan(sleep=lambda _: None)
    return Adapters(
        orders=MockOrderAdapter(generate(seed=1, customers=1), faults),
        carrier=MockCarrierAdapter(faults=faults),
        payment=MockPaymentAdapter(faults),
        inventory=MockInventoryAdapter({}, faults),
        notification=MockNotificationAdapter(faults),
    )


@contextmanager
def open_harness(
    database_url: str,
    engine: Engine,
    agent_llm: LLMClient | None = None,
    customer_llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
) -> Iterator[Harness]:
    adapters = mock_adapters()
    with PostgresSaver.from_conn_string(database_url) as saver:
        saver.setup()
        registry = GraphRegistry(
            config_dir() / "graphs",
            get_settings().graph_active_version,
            partial(build_handlers, llm=agent_llm),
            make_feasibility(adapters.inventory, adapters.carrier),
            saver,
        )
        runner = CaseRunner(registry, engine)
        yield Harness(
            engine,
            runner,
            CaseService(runner, agent_llm),
            adapters,
            agent_llm,
            customer_llm,
            judge_llm,
        )
