# Autonomous Product Return Resolution Agent — System Architecture

> Status: design v1 · Scope: full system, with MVP cut-lines marked **[MVP]** / **[Phase 2]**

> Implementation note: the procedural graph runtime is built on **LangGraph** (graphs stay versioned JSON, compiled to a LangGraph `StateGraph` with Postgres checkpoints). Where this design and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) differ, the plan and the code are the source of truth.

---

## 0. One-paragraph summary

A customer describes a return problem in natural language (web chat / WhatsApp / email). A **Procedural Graph Runtime** moves the case through a versioned graph of allowed steps. At each decision point the **Decision Intelligence (DI) layer** — powered by a **Reason → Search → Infer** engine — extracts facts, lets a deterministic **Policy Engine** decide eligibility, enumerates and scores legal resolutions, estimates confidence and risk, and routes the case to **auto-execute**, **human approval**, or **escalation**. An **Execution layer** carries out refunds, pickups, replacements and exchanges through adapters with idempotency guarantees, while a **Lifecycle service** tracks shipments/refunds and sends notifications and reminders. Every step writes a tamper-evident **Decision Record** (graph path + policy clauses + scores + evidence), which powers explanations, audit, case summaries and analytics. Offline, a bounded **Recursive Self-Improvement (RSI) loop** learns from human overrides and outcomes to improve node playbooks and propose graph edits — which only go live after replay validation and admin approval.

---

## 1. Design principles

| # | Principle | Consequence in the design |
|---|---|---|
| P1 | **LLM proposes, policy engine decides** | Eligibility, refund amounts, thresholds are deterministic code. LLM extracts, explains, converses. |
| P2 | **Graph-constrained autonomy** | The agent can only take transitions that exist in the active graph version; off-graph actions are rejected. |
| P3 | **Autonomy ∝ risk × value × confidence** | Admin-configured gate decides auto / approval / escalate. |
| P4 | **Every decision has a receipt** | One `DecisionRecord` serves explanations, audit trail and case summaries. |
| P5 | **Safe money movement** | Idempotency keys, transactional outbox, compensation; refund ≤ paid; one refund per item. |
| P6 | **Integrate, don't replace** | All external systems behind adapter interfaces; MVP ships mock adapters. |
| P7 | **Bounded self-improvement** | Policy, money thresholds, invariants, verifier, eval suite and audit log are *outside* the evolvable surface. |
| P8 | **Prevention is a feature** | Return-reason intelligence per SKU/size feeds merchandising. |

---

## 2. System context

```
                ┌────────────┐   ┌────────────┐   ┌────────────┐
  Actors        │  Customer  │   │  Support   │   │   Admin /  │   Warehouse/QC
                │ (web/WA/   │   │  Agent /   │   │  Policy    │   operator
                │  email)    │   │  Approver  │   │  owner     │
                └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
                      │                │                │
              ┌───────▼────────────────▼────────────────▼────────┐
              │        RETURN RESOLUTION AGENT PLATFORM           │
              └───┬─────────┬──────────┬──────────┬─────────┬────┘
                  │         │          │          │         │
  External     ┌──▼──┐  ┌───▼───┐  ┌───▼───┐  ┌───▼───┐  ┌──▼────────┐
  systems      │ OMS │  │Carrier│  │Payment│  │Catalog│  │Notification│
  (adapters)   │     │  │(pickup│  │gateway│  │Invent.│  │ email/SMS/ │
               │     │  │ track)│  │refunds│  │       │  │ WhatsApp   │
               └─────┘  └───────┘  └───────┘  └───────┘  └───────────┘
                         Claude API (LLM + vision)
```

---

## 3. Logical architecture

```
┌──────────────────────────────── EXPERIENCE LAYER ────────────────────────────────┐
│  Customer Portal + Chat     │  Support Console (cases, approvals,  │  Admin Studio  │
│  (Next.js, WhatsApp, email) │  timeline, AI summary, analytics)    │ (policy, graph,│
│                             │                                      │  thresholds,   │
│                             │                                      │  RSI proposals)│
└──────────────┬──────────────┴──────────────────┬───────────────────┴───────┬───────┘
               │  REST + WebSocket (FastAPI)     │                           │
┌──────────────▼─────────────────────────────────▼───────────────────────────▼───────┐
│ API GATEWAY  — authN (customer OTP / staff SSO-JWT), RBAC, rate limit, PII masking   │
└──────────────┬──────────────────────────────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────── CORE (online) ──────────────────────────────┐
│                                                                                     │
│  ┌─────────────────────┐    ┌──────────────────────────────────────────────────┐    │
│  │ Conversation        │◄──►│ PROCEDURAL GRAPH RUNTIME                         │    │
│  │ Orchestrator        │    │  locate node → load 2-hop neighbourhood →        │    │
│  │ (turns, channels,   │    │  prune by conditions/invariants → execute node   │    │
│  │  context assembly)  │    │  contract → validate → transition → persist      │    │
│  └─────────────────────┘    └───────┬──────────────────────────────────────────┘    │
│                                     │ decision nodes                                │
│  ┌──────────────────────────────────▼───────────────────────────────────────────┐   │
│  │ DECISION INTELLIGENCE LAYER   (engine: Reason → Search → Infer)              │   │
│  │  Reason: fact extraction ─► Policy Engine (deterministic)                    │   │
│  │  Search: option enumeration ─► scoring ─► 1–2 step look-ahead                │   │
│  │  Infer : confidence (self-consistency) · risk · evidence ─► Autonomy Gate    │   │
│  │  Explain: DecisionRecord (path, clauses, scores, evidence)                   │   │
│  └───┬──────────────┬──────────────┬───────────────┬────────────────────────────┘   │
│      │              │              │               │                                │
│  ┌───▼─────┐  ┌─────▼──────┐  ┌────▼─────┐  ┌──────▼───────┐  ┌──────────────────┐  │
│  │ Policy  │  │ Evidence   │  │ Risk     │  │ Approval &   │  │ Execution Service│  │
│  │ Engine  │  │ Service    │  │ Service  │  │ Escalation   │  │ (outbox, saga,   │  │
│  │ (rules, │  │ (vision,   │  │ (signals,│  │ (queues, SLA,│  │  idempotency)    │  │
│  │ versions│  │ authentic.)│  │ score)   │  │  handoff pkt)│  │  ─► Adapters     │  │
│  └─────────┘  └────────────┘  └──────────┘  └──────────────┘  └────────┬─────────┘  │
│                                                                        │            │
│  ┌──────────────────────────┐   ┌───────────────────────┐   ┌──────────▼─────────┐  │
│  │ Lifecycle & Notification │◄──│ Event Bus (domain     │◄──│ Adapter webhooks   │  │
│  │ (trackers, timers, SLA,  │   │ events, outbox relay) │   │ (carrier, payment) │  │
│  │  reminders, follow-ups)  │   └───────────────────────┘   └────────────────────┘  │
│  └──────────────────────────┘                                                       │
│  ┌──────────────────────────┐   ┌───────────────────────┐                           │
│  │ Audit Log (hash-chained, │   │ Analytics & Reporting │                           │
│  │ append-only)             │   │ (reasons, trends, SLA)│                           │
│  └──────────────────────────┘   └───────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────── LEARNING (offline, gated) ──────────────────────────────┐
│ Signal collector ─► Reflector ─► Curator (L1 playbooks) / Graph Refiner (L2) /       │
│ Prompt optimizer (L3) ─► Structural checks ─► Replay on eval suite ─► Admin approve │
│ ─► new versioned playbook/graph                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────── DATA ───────────────────────────────────────────┐
│ PostgreSQL (OLTP + pgvector)  ·  Object storage (evidence)  ·  Redis (queue, locks)  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component specifications

### 4.1 Conversation Orchestrator **[MVP]**
- **Responsibility:** receive a turn from any channel, load the case, assemble LLM context, call the Graph Runtime, stream the reply.
- **Context assembly (fixed order, for prompt-cache stability):**
  1. Frozen system prompt (persona, global rules, AI disclosure) — cached.
  2. Stable tool list (all tools; node-level permissions enforced at runtime, not by changing the list — keeps cache warm).
  3. Conversation history (customer text only in `user` role — never as instructions).
  4. Per-node guidance (node task + 2-hop neighbourhood + edge guidance/pitfalls + relevant playbook entries) injected as a **mid-conversation system message** — the operator channel, which keeps customer text from overriding instructions and preserves the cached prefix.
- **Channels:** web chat (WebSocket) **[MVP]**, email, WhatsApp **[Phase 2]** via a `ChannelAdapter` interface.
- **Language:** responds in the customer's language; Hinglish supported via the LLM **[MVP]**.

### 4.2 Procedural Graph Runtime **[MVP]**
See §5 for the full spec. Owns `case.current_node`, executes node contracts, enforces invariants, performs 2-hop look-ahead, persists every transition.

### 4.3 Decision Intelligence Layer **[MVP]**
See §6. Runs at decision nodes (`CHECK_ELIGIBILITY`, `GENERATE_OPTIONS`, `SCORE_OPTIONS`, `AUTONOMY_GATE`) and supplies confidence to every LLM node.

### 4.4 Policy Engine **[MVP]**
- **Rules as data:** YAML policies, each rule with a stable `clause_id`, human-readable text, and a machine condition in **JSONLogic** (same expression language as graph edge conditions).
- **Versioned & effective-dated:** a case is evaluated against the policy version active **on the order date**.
- **Output:** `{eligible, allowed_resolutions[], required_evidence[], fees, window_remaining, trace[{clause_id, result, inputs}]}`.
- **What-if simulator [Phase 2]:** re-run a candidate policy against historical cases and show which decisions would flip.

Example rule:
```yaml
- clause_id: RET-WINDOW-01
  text: "Items may be returned within 30 days of delivery."
  applies_to: { category_not_in: [perishable, innerwear] }
  condition: { "<=": [ { "var": "days_since_delivery" }, 30 ] }
  on_fail: { eligible: false, reason_code: OUTSIDE_WINDOW }
```

### 4.5 Evidence Service **[MVP: assess · Phase 2: authenticity]**
- Upload → virus/type check → object storage → `Evidence` row.
- **Assessment (LLM vision, structured output):** `{defect_type, visible_damage, matches_catalog_item, condition_grade (A–D), confidence, notes}`; compares against catalogue images.
- **Authenticity checks:** EXIF/metadata presence & timestamp vs delivery date, perceptual-hash duplicate detection across cases, catalogue-image reuse detection, AI-generation signals; high-risk → request live photo/video.
- Output is **advisory** — feeds risk and the gate; never auto-rejects on its own.

### 4.6 Risk Service **[MVP: rules · Phase 2: model]**
- **Signals:** return frequency/ratio, account age, high-value item, return initiated before/at delivery, linked accounts (email/phone/address/device), prior confirmed fraud, evidence authenticity flags, mismatch between stated reason and evidence, serial "damaged" claims, COD + high value.
- **Score:** weighted rules → `risk ∈ [0,1]` + list of contributing signals (explainable). Phase 2: gradient-boosted model calibrated on confirmed outcomes.
- **Fairness:** no protected attributes or close proxies; signal list reviewed by admin.

### 4.7 Approval & Escalation Service **[MVP]**
- Queues: `approval` (consequential actions above threshold) and `escalation` (complex/high-risk/dispute/legal/angry).
- **Handoff packet:** AI case summary, timeline, decision record, evidence thumbnails, risk signals, suggested action + alternatives — so the human never asks the customer to repeat.
- Approver actions: approve / modify / reject (with reason code) → reason codes become RSI learning signals.
- SLA timers per queue; auto-escalate to supervisor on breach.
- **Right to human review:** customer can request human review of any automated denial (GDPR Art. 22 alignment).

### 4.8 Execution Service **[MVP with mock adapters]**
- **Transactional outbox:** a node transition and its side-effect intent are committed in one DB transaction; a worker executes the intent against the adapter.
- **Idempotency key:** `hash(case_id, action_type, return_item_id)` — retries can never double-refund.
- **Saga/compensation:** e.g., replacement order created but pickup fails 3× → cancel replacement / hold refund → escalate.
- **Adapters (interfaces):** `OrderAdapter`, `CarrierAdapter` (schedule pickup, track, doorstep-QC checklist), `PaymentAdapter` (refund to source / UPI / store credit), `InventoryAdapter` (stock check for exchange/replacement), `NotificationAdapter`.
- **Hard guards in code:** refund ≤ amount paid for the item (pro-rated for coupons/BOGO); one refund per item; approval token required when gate demanded approval.

### 4.9 Lifecycle & Notification Service **[MVP]**
- Subscribes to domain events (`CaseStateChanged`, `PickupScheduled`, `ShipmentScanned`, `ItemReceived`, `QCCompleted`, `RefundIssued`, `ReplacementShipped`…).
- **Notifications** are side-effects of transitions (not graph nodes); templated + LLM-personalised, always generated *from* the decision record.
- **Timers:** pickup reminders, "hand over item" nudges, evidence-request follow-ups, refund-ETA updates, auto-close after inactivity.
- **SLA clocks:** first response, resolution time, refund TAT, and statutory clocks (e.g., India grievance acknowledgment 48 h / resolution 1 month).

### 4.10 Audit Log **[MVP]**
- Append-only `AuditEvent` rows, each storing `prev_hash` and `hash` (hash chain → tamper-evident).
- Records every AI action (model, prompt version, graph version, playbook version, inputs hash, output, confidence) and every human action (who, role, what, why).

### 4.11 Analytics & Reporting **[MVP: core · Phase 2: advanced]**
- Return-reason distribution (by SKU / size / category / channel), refund vs exchange mix, revenue retained, cost per return, auto-resolution rate, escalation rate, approval overrides, SLA compliance, resolution-time percentiles, fraud flags vs confirmed.
- **Prevention insights:** "SKU X size M: 41% 'runs small'" → merchandising export.

### 4.12 Learning Service (bounded RSI) **[MVP: L1 · Phase 2: L2, L3]**
See §7.

---

## 5. Procedural Graph — specification

### 5.1 Nodes (25)

| Group | Node | Kind | Notes |
|---|---|---|---|
| Entry | `START` | code | channel + customer resolved |
| | `AUTHENTICATE` | code | OTP / session; invariant gate for all order data |
| | `IDENTIFY_ORDER` | code+LLM | match order/items from text or picker |
| Intake | `UNDERSTAND_REQUEST` | LLM | structured extraction, self-consistency k=3 |
| | `CLARIFY` | LLM | asks only for missing required slots; loops back |
| Policy | `CHECK_ELIGIBILITY` | code | Policy Engine |
| | `EXPLAIN_INELIGIBLE` | LLM | cites clause; offers alternatives / human review |
| Evidence | `REQUEST_EVIDENCE` | LLM | when policy/risk requires it |
| | `ASSESS_EVIDENCE` | LLM-vision + checks | condition + authenticity |
| Decision (DI) | `RISK_SCORE` | code/model | |
| | `GENERATE_OPTIONS` | code | legal resolutions from policy output |
| | `SCORE_OPTIONS` | DI | utility scoring + look-ahead |
| | `AUTONOMY_GATE` | code | auto / approval / escalate |
| | `HUMAN_APPROVAL` | human | approver queue |
| | `CUSTOMER_CONFIRM` | customer | customer picks / accepts offer |
| Execution | `SCHEDULE_PICKUP` | code | carrier adapter |
| | `TRACK_SHIPMENT` | event-driven | waits on carrier events |
| | `INSPECT_QC` | human/code | warehouse or doorstep QC |
| | `ISSUE_REFUND` | code | payment adapter, guarded |
| | `CREATE_REPLACEMENT` | code | order adapter |
| | `CREATE_EXCHANGE` | code | inventory + order adapter |
| | `KEEP_ITEM_REFUND` | code | returnless refund |
| Exceptions | `DISPUTE` | human | contested decision / QC mismatch |
| | `ESCALATE` | human | complex / high-risk |
| End | `CLOSE` | terminal | resolved / rejected / cancelled |

### 5.2 Node contract (STAGE-style)

```json
{
  "id": "ASSESS_EVIDENCE",
  "kind": "llm_vision",
  "task": "Assess whether the photos show the claimed defect on the ordered item.",
  "inputs":  ["case.items[*]", "evidence[*]", "catalog.images"],
  "output_schema": "EvidenceAssessment",
  "allowed_tools": ["get_catalog_images", "run_authenticity_checks"],
  "validators": ["schema", "confidence_range", "item_id_in_case"],
  "on_failure": { "retry": 1, "then": "ESCALATE" },
  "timeout_s": 30,
  "policy_refs": ["EVID-DAMAGE-02"]
}
```

### 5.3 Edge schema

```json
{
  "from": "SCORE_OPTIONS",
  "to": "AUTONOMY_GATE",
  "relation": "next",
  "condition": { "!!": [ { "var": "decision.best_option" } ] },
  "guidance": "Present at most 2 options; lead with exchange when reason is size/fit.",
  "pitfalls": ["Do not quote a refund amount before refund is computed by code."],
  "priority": 10
}
```

### 5.4 Global invariants (hard, code-enforced, never evolvable)

| ID | Invariant |
|---|---|
| INV-1 | No order/PII disclosure before `AUTHENTICATE` succeeds |
| INV-2 | No resolution offer before `CHECK_ELIGIBILITY` returns eligible |
| INV-3 | `ISSUE_REFUND` / `KEEP_ITEM_REFUND` require an approval token when the gate returned `approval` |
| INV-4 | Refund amount ≤ paid amount for the item(s), computed by code |
| INV-5 | At most one refund per return item (idempotency) |
| INV-6 | Outbound messages may not contain commitments absent from the DecisionRecord (verifier) |
| INV-7 | A case cannot reach `CLOSE(resolved)` without a terminal execution success event |

### 5.5 Runtime loop

```
on_event(case, event):                     # customer turn, webhook, timer, human action
    node = graph.node(case.current_node)
    nbhd = graph.neighbourhood(node, hops=2)
    viable = [p for p in nbhd.paths(len<=2)
              if conditions_hold(p, case.state)
              and not violates_invariants(p, case.state)
              and second_step_feasible(p, case.state)]   # look-ahead (e.g. stock, carrier serviceability)
    result = execute_contract(node, case, guidance=render(viable))
    validate(result, node.validators)  or  handle_failure(node)
    next_edge = DI.choose(viable, result)  if node.is_decision  else  first_true(viable)
    with transaction:
        case.state.apply(result); case.current_node = next_edge.to
        outbox.enqueue(side_effects(next_edge)); audit.append(...)
    if action_off_graph: relocate_on_full_graph() or goto ESCALATE
```

### 5.6 Two-step look-ahead examples
- At `SCORE_OPTIONS`, option *exchange* → `CREATE_EXCHANGE` requires stock: inventory check fails → option pruned **before** it is offered.
- Option *pickup return* → `SCHEDULE_PICKUP` requires serviceable pincode: not serviceable → switch to drop-off or keep-item.
- At `CHECK_ELIGIBILITY` → `REQUEST_EVIDENCE` → `ASSESS_EVIDENCE`: if the category never requires evidence, skip directly to `RISK_SCORE`.

### 5.7 Graph lifecycle
`Draft (LLM-assisted from policy, each node mapped to source clauses) → Structural validation → Replay on eval suite → Admin approval → Frozen vN (active) → (RSI proposes vN+1)`. Every case pins the graph version it started on.

---

## 6. Decision Intelligence layer — specification

### 6.1 Engine stages

| Stage | What it does | Implementation |
|---|---|---|
| **Reason** | Extract facts (items, reason category, desired outcome, sentiment, condition claims) | LLM structured output, k=3 samples in parallel; Policy Engine decides eligibility |
| **Search** | Enumerate legal options; compute utility; look ahead 1–2 steps for feasibility | Deterministic code + inventory/carrier checks; LLM only for preference signals |
| **Infer** | Confidence, risk, evidence reliability → gate | Self-consistency agreement, risk service, evidence service |
| **Explain** | Produce `DecisionRecord` | Deterministic assembly + LLM-written plain-language rationale (verified) |

### 6.2 Option utility

For each legal option *o* ∈ {exchange, replacement, refund, store_credit, keep_item_refund, reject, ask_more}:

```
U(o) = w_rev·RevenueRetained(o)
     − w_cost·NetCost(o)            # reverse shipping + handling + expected write-down − resale recovery
     + w_cx·CustomerFit(o)          # matches stated desire, speed, history/tier
     − w_risk·Risk·Exposure(o)      # exposure = money released before item is verified
```
All terms normalised to [0,1]; weights are admin-configured per merchant. `keep_item_refund` becomes optimal automatically when reverse cost > recovery value.

### 6.3 Confidence
`confidence = min(extraction_agreement, evidence_confidence, identification_confidence)`; rule-based decisions have confidence 1.0. Extraction agreement = share of the k=3 samples that agree on (item, reason_category, desired_outcome). Model self-reported confidence is **not** used alone.

### 6.4 Autonomy gate (defaults, admin-editable)

| Condition | Route |
|---|---|
| risk < 0.3 **and** value < ₹5,000 **and** confidence ≥ 0.8 **and** no flags | **AUTO** |
| risk < 0.6 **and** (value ≥ ₹5,000 **or** 0.6 ≤ confidence < 0.8) | **APPROVAL** |
| risk ≥ 0.6 **or** confidence < 0.6 **or** legal threat / dispute / repeated anger | **ESCALATE** |

### 6.5 DecisionRecord
```json
{
  "case_id": "...", "node": "AUTONOMY_GATE", "graph_version": "v7", "policy_version": "2026-09",
  "facts": {...}, "policy_trace": [{"clause_id":"RET-WINDOW-01","result":true}],
  "options": [{"type":"exchange","U":0.82,"terms":{...}}, {"type":"refund","U":0.61}],
  "chosen": "exchange", "lookahead": ["CREATE_EXCHANGE: stock ok"],
  "confidence": 0.91, "risk": {"score":0.12,"signals":["new_account"]},
  "route": "AUTO", "rationale_text": "...", "actor": "ai", "model": "claude-opus-5-5"
}
```

---

## 7. Bounded Recursive Self-Improvement

### 7.1 Learning signals
Human override / approval rejection (with reason code) · reopened or disputed case · fraud confirmed at QC · verifier-caught policy violation · customer CSAT / thumbs · SLA breach.

### 7.2 Levels

| Level | What evolves | How | Go-live gate |
|---|---|---|---|
| **L1 Playbooks** **[MVP]** | Per-node guidance & pitfalls (ACE-style Generator/Reflector/Curator) | Nightly batch over signals; retrieved selectively per node | Staged → eval replay → auto-promote if no metric regresses |
| **L2 Graph edits** **[Phase 2]** | Add/prune nodes & edges, edge attributes (Procedural-Graphs refiner) | Contrast failed vs successful trajectories → JSON edit ops | Structural checks → replay → statistical test → **admin approval** → vN+1 |
| **L3 Prompts** **[Phase 2]** | Node prompts (GEPA-style reflective evolution) | Offline against eval suite | Replay → admin approval |

### 7.3 Frozen surface (never self-modified)
Policy rules · money thresholds & gate bounds · invariants INV-1..7 · verifier · eval suite · audit log · risk-signal whitelist.

### 7.4 Release gates (all must hold)
Policy-violation rate = 0 · refund leakage not higher · pass^k not lower · escalation rate within band · no fairness-metric regression.

---

## 8. Data model (core tables)

```
customer(id, name, phone, email, tier, created_at, risk_profile_json)
address(id, customer_id, pincode, ...)
order(id, customer_id, placed_at, delivered_at, payment_method, total, currency, status)
order_item(id, order_id, sku, variant, qty, unit_price, discount_alloc, final_sale)
product(sku, title, category, attributes_json, images[])
policy(id, version, effective_from, yaml, status)        policy_rule(clause_id, policy_id, text, condition_json)
graph(id, version, status, json, created_by, approved_by)
playbook_entry(id, node_id, text, source_case_ids[], version, status, embedding vector)
return_case(id, customer_id, order_id, channel, current_node, graph_version, policy_version,
            status, priority, sla_due_at, created_at, closed_at)
return_item(id, case_id, order_item_id, qty, reason_category, reason_text, condition_grade, resolution)
message(id, case_id, role, channel, content, created_at)
evidence(id, case_id, return_item_id, uri, mime, phash, exif_json, assessment_json)
decision_record(id, case_id, node, json, created_at)
approval(id, case_id, decision_id, requested_action, status, approver_id, reason_code, decided_at)
shipment(id, case_id, carrier, awb, pickup_slot, status, events_json)
refund(id, case_id, amount, method, idempotency_key UNIQUE, status, gateway_ref)
replacement_order(id, case_id, new_order_id, status)     exchange(id, case_id, from_variant, to_variant, status)
outbox(id, case_id, action, payload, idempotency_key UNIQUE, status, attempts)
notification(id, case_id, channel, template, status, sent_at)
sla_timer(id, case_id, kind, due_at, fired_at)
audit_event(id, case_id, actor_type, actor_id, action, payload_json, prev_hash, hash, created_at)
user(id, role, ...)   -- staff: agent | approver | admin | qc_operator | analyst
```

---

## 9. Security, privacy & compliance

| Area | Control |
|---|---|
| **RBAC** | customer (own cases) · agent (assigned cases, no refunds > limit) · approver (approve within limit) · admin (policy/graph/thresholds) · qc_operator (QC only) · analyst (aggregates, masked PII) |
| **PII** | Field-level encryption for phone/address/bank-UPI; masked in UI by default; **redacted before LLM calls** (tokenised placeholders re-hydrated after) |
| **Payments** | No card data stored; refunds via gateway tokens; UPI/bank details for COD refunds encrypted, write-only |
| **Prompt injection** | Customer text only in `user` role; operator guidance via system channel; tool permissions enforced by runtime per node; document/image text treated as data |
| **AI disclosure** | Chat states it is an AI assistant (EU AI Act Art. 50) with a one-click "talk to a human" |
| **Human review** | Any automated denial can be contested → human review queue |
| **India** | Consumer Protection (E-Commerce) Rules 2020 grievance clocks in SLA engine; DPDP-aligned consent, purpose limitation, retention policies, breach-notification runbook |
| **Audit** | Hash-chained append-only log; exportable per case |
| **Transport/at rest** | TLS everywhere; DB + object storage encryption; secrets in env/secret manager |

---

## 10. LLM usage plan (Claude API)

Default model: **`claude-opus-5-5`** on every route, with effort tuned per route (cheaper models such as Sonnet 5.5 / Haiku 4.5 are an optional, eval-measured cost lever later — not the default).

| Route | Effort | API features |
|---|---|---|
| `UNDERSTAND_REQUEST` (k=3 parallel) | low | Structured outputs (`messages.parse` / `output_config.format`) |
| `CLARIFY`, customer replies | low | Streaming; mid-conversation system message for node guidance |
| `ASSESS_EVIDENCE` | medium | Vision (image blocks), structured outputs |
| Tool-using steps | medium | Strict tools (`strict: true`), `tool_choice: auto` (forced tool choice is not supported on this model) |
| Outbound-message verifier (INV-6) | low | Structured outputs (`{violations: [...]}`) |
| Case summary / escalation packet | low | Structured outputs |
| RSI reflector / curator / graph refiner | high | Message Batches for replay & reflection (async, 50% cost) |

Cross-cutting: adaptive thinking (default on this model); **prompt caching** of the frozen system prompt + tools (verify via `usage.cache_read_input_tokens`); server-side refusal **fallbacks** enabled; typed error handling with retries for 429/5xx; per-call logging of model, effort, tokens, latency into the audit event.

---

## 11. Evaluation & observability

- **Eval suites:** (a) **τ²-bench retail domain** (open-source returns/exchanges with a written policy) as an external benchmark; (b) an internal synthetic suite of ~200 cases covering India specifics (COD refunds, RTO, doorstep QC, Hinglish), fraud scenarios (AI-edited photos, empty box, serial returner), partial/multi-item/coupon pro-ration, out-of-stock exchanges, carrier failures.
- **Metrics:** pass^1 and **pass^k** (consistency), policy-violation rate (target 0), correct-route rate (auto/approval/escalate), refund-amount exactness, clarification efficiency (turns to resolution), auto-resolution rate, escalation precision, cost & latency per case.
- **Tracing:** OpenTelemetry spans per node + LLM call (Langfuse for LLM traces); dashboards for gate distribution, override rate, SLA.

---

## 12. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js + TypeScript, Tailwind, shadcn/ui, **React Flow** (graph view & diff), Recharts | One codebase for portal/console/studio; React Flow makes the procedural graph and RSI diffs visual |
| Backend | Python 3.12, **FastAPI**, Pydantic v2, SQLAlchemy | Best AI ecosystem; typed schemas shared with structured outputs |
| LLM | Anthropic Python SDK (`claude-opus-5-5`) | Tool use, structured outputs, vision, caching, batches |
| Rules / conditions | JSONLogic (`json-logic`) + YAML | One expression language for policy rules and edge conditions |
| Data | PostgreSQL 16 + pgvector, S3-compatible storage (MinIO locally) | OLTP + playbook retrieval + evidence |
| Async | Redis + ARQ workers, outbox relay, scheduler for timers | Simple, reliable background execution |
| Observability | OpenTelemetry, Langfuse | Node-level + LLM tracing |
| Deploy | Docker Compose (dev/demo) | One-command demo |

---

## 13. Repository structure

```
/apps
  /web                  # Next.js: customer portal, support console, admin studio
/services
  /api                  # FastAPI app (routers, auth, RBAC)
  /core
    /graph              # runtime, contracts, invariants, look-ahead, versioning
    /di                 # reason / search / infer / explain, gate
    /policy             # engine, YAML loader, what-if
    /evidence           # vision assessment, authenticity checks
    /risk               # signals, scoring
    /execution          # outbox, saga, idempotency
    /lifecycle          # events, timers, SLA, notifications
    /audit              # hash-chained log
    /analytics
    /llm                # Claude client wrapper, prompts, schemas, verifier
  /adapters             # oms, carrier, payment, inventory, notification (+ mocks)
  /learning             # signals, reflector, curator, graph refiner, replay
  /workers              # ARQ worker entrypoints
/config
  /policies/*.yaml  /graphs/*.json  /gate.yaml
/evals                  # synthetic cases, tau2 adapter, metrics
/infra                  # docker-compose, seed data
/docs                   # this file
```

---

## 14. Key flows

**A. Happy path — size issue → exchange (AUTO)**
`START → AUTHENTICATE → IDENTIFY_ORDER → UNDERSTAND_REQUEST ("too small, want L") → CHECK_ELIGIBILITY ✓ → RISK_SCORE 0.08 → GENERATE_OPTIONS {exchange, refund} → SCORE_OPTIONS (look-ahead: size L in stock ✓; exchange U=0.84) → AUTONOMY_GATE AUTO → CUSTOMER_CONFIRM → CREATE_EXCHANGE + SCHEDULE_PICKUP → notifications → TRACK_SHIPMENT → INSPECT_QC ✓ → CLOSE`

**B. High-value damaged phone (APPROVAL)**
`… → REQUEST_EVIDENCE → ASSESS_EVIDENCE (crack visible, authentic, conf 0.86) → RISK 0.25 → options {replacement, refund} → value ₹42k ≥ threshold → HUMAN_APPROVAL (packet: summary, photos, clauses) → approve replacement → CREATE_REPLACEMENT → …`

**C. Suspicious evidence (ESCALATE)**
`ASSESS_EVIDENCE: no EXIF, perceptual hash matches another account's claim, damage pattern inconsistent → RISK 0.78 → ESCALATE (no accusation to customer; neutral "specialist review" message) → human decides`

**D. Out-of-window request**
`CHECK_ELIGIBILITY ✗ (RET-WINDOW-01, 41 days) → EXPLAIN_INELIGIBLE (cites clause; offers warranty route if defect; "request human review" button) → CLOSE(rejected) or ESCALATE`

**E. Pickup fails 3× (saga)**
`TRACK_SHIPMENT: 3 failed attempts → compensation: hold refund / cancel replacement → notify customer with drop-off option → ESCALATE if no response in 48h`

---

## 15. Deliverable coverage map

| PS deliverable | Component(s) |
|---|---|
| Customer authentication & profile | API gateway, `AUTHENTICATE`, customer table |
| Order & product retrieval | OrderAdapter, `IDENTIFY_ORDER` |
| Return-request creation · reason collection | Orchestrator, `UNDERSTAND_REQUEST`, `return_case/return_item` |
| Eligibility verification · policy evaluation | Policy Engine, `CHECK_ELIGIBILITY` |
| AI request understanding · reason classification | DI Reason stage |
| Image/document evidence · condition assessment | Evidence Service |
| Return / replacement / refund / exchange workflows | Execution subgraph + adapters |
| AI resolution recommendations | DI Search stage (`SCORE_OPTIONS`) |
| Automated communication · clarification questions | Orchestrator, `CLARIFY`, Lifecycle notifications |
| Pickup scheduling · shipment / refund / replacement tracking · exchange mgmt | Execution + Lifecycle services |
| Exception & dispute handling · human escalation | `DISPUTE`, `ESCALATE`, Approval & Escalation service |
| Human approval for consequential actions | Autonomy gate + INV-3 + approval queue |
| Natural-language support | Orchestrator (multilingual) |
| Notifications · follow-ups & reminders | Lifecycle timers |
| Return history · order & return timeline | Case timeline view (messages + decisions + events) |
| Support dashboard · AI case summaries | Support Console, escalation packet |
| Policy & decision explanations | DecisionRecord + policy trace |
| Analytics · reason analysis · trends · SLA tracking | Analytics service, SLA timers |
| Admin policy & workflow configuration | Admin Studio (policy YAML, graph editor, gate thresholds) |
| RBAC | Gateway + role matrix |
| Audit trail of AI & user actions | Hash-chained Audit Log |
| Secure data handling | §9 controls |

---

## 16. Delivery phases

| Phase | Scope |
|---|---|
| **MVP (demo-ready)** | Web chat + support console + admin (policy/thresholds, read-only graph view) · graph runtime with all 25 nodes · policy engine · DI layer with gate & decision records · evidence assessment (vision) · rule-based risk · mock adapters (OMS/carrier/payment/inventory) · lifecycle notifications & timers · audit log · core analytics · L1 playbook learning · synthetic eval suite with pass^k |
| **Phase 2** | WhatsApp/email channels · evidence authenticity checks · ML risk model · L2 graph evolution with visual diff + admin approval · L3 prompt optimisation · policy what-if simulator · τ²-bench integration · real carrier/payment sandbox adapters |
| **Phase 3** | Multi-merchant / marketplace tenancy · disposition routing (restock/refurb/liquidate) · merchandising export · voice channel |
