# Autonomous Product Return Resolution Agent — Production Implementation Plan

> Version 1.1 · Target: production-grade MVP in ~14 weeks (7 two-week sprints) with a team of 4, followed by Phase 2.
> Changes in 1.1: procedural graph executed on **LangGraph** (graph stays versioned JSON); LangChain not used; post-MVP "Phase 3" backlog (marketplace, disposition, voice) removed; security & compliance (§14) trimmed to essentials. (Build step "Phase 3 — graph runtime" in §8 is unrelated and stays.)
> Scale the timeline linearly for a smaller team; the **order of work must not change** (each phase depends on the previous one).

---

## Table of contents

1. [Scope, goals and non-goals](#1-scope-goals-and-non-goals)
2. [Success criteria and SLOs](#2-success-criteria-and-slos)
3. [Key decisions (ADR summary)](#3-key-decisions-adr-summary)
4. [Team, roles and working agreements](#4-team-roles-and-working-agreements)
5. [Repository structure](#5-repository-structure)
6. [Environments, CI/CD and DevOps](#6-environments-cicd-and-devops)
7. [Timeline and milestones](#7-timeline-and-milestones)
8. [Work breakdown — phase by phase](#8-work-breakdown--phase-by-phase)
9. [Data model](#9-data-model)
10. [API surface](#10-api-surface)
11. [LLM integration (DeepSeek-V4.1-Flash)](#11-llm-integration-deepseek-v41-flash)
12. [Risk, fraud and imperfect-evidence handling](#12-risk-fraud-and-imperfect-evidence-handling)
13. [Policy precedence and overrides](#13-policy-precedence-and-overrides)
14. [Security, privacy and compliance](#14-security-privacy-and-compliance)
15. [Testing strategy and quality gates](#15-testing-strategy-and-quality-gates)
16. [Observability, alerting and runbooks](#16-observability-alerting-and-runbooks)
17. [Rollout plan](#17-rollout-plan)
18. [Risk register](#18-risk-register)
19. [Definition of Done and launch checklist](#19-definition-of-done-and-launch-checklist)
20. [Requirements traceability](#20-requirements-traceability)
21. [Phase 2 backlog](#21-phase-2-backlog)

---

## 1. Scope, goals and non-goals

### 1.1 Product goal
An AI agent that resolves e-commerce return, refund, replacement and exchange requests end to end — understanding the request, verifying eligibility against policy, assessing evidence, choosing and executing the best resolution, tracking it to closure — while escalating risky or uncertain cases to humans and explaining every decision.

### 1.2 Architecture in one line
**Procedural Graph** (allowed steps, versioned JSON executed on LangGraph) + **Decision Intelligence layer** powered by **Reason → Search → Infer** (makes and explains decisions) + **bounded Recursive Self-Improvement** (learns from outcomes, gated by tests and admin approval).

### 1.3 In scope (MVP)
- Web chat channel, support console, admin studio.
- All 25 graph nodes, policy engine, DI layer, autonomy gate, decision records.
- Evidence assessment (deterministic checks + vision), rule-based risk scoring.
- Execution for refund, replacement, exchange, keep-item refund, pickup — against **mock adapters** with production-shaped interfaces.
- Lifecycle tracking, notifications (email + in-app), reminders, SLA clocks.
- Approval and escalation queues with handoff packets and AI case summaries.
- Hash-chained audit log, RBAC, PII protection.
- Core analytics (reasons, trends, SLA, automation rates, prevention insights).
- L1 self-improvement (per-node playbooks).
- Full test pyramid, eval suite with pass^k, red-team suite, shadow mode.

### 1.4 Non-goals (MVP)
- Deferred to Phase 2 (§21): WhatsApp/email channels, ML risk model, graph auto-evolution (L2), prompt optimisation (L3), policy what-if simulator, real carrier/payment integrations.
- Out of scope entirely: multi-merchant/marketplace tenancy, disposition routing, voice channel.

### 1.5 Assumptions
- Single merchant (single tenant).
- Currency INR (multi-currency-ready schema), timezone stored UTC, displayed IST.
- LLM: **DeepSeek-V4.1-Flash** for both vision and reasoning, accessed behind an `LLMClient` interface.

---

## 2. Success criteria and SLOs

### 2.1 Quality bars (release-blocking)

| Metric | Target | Measured by |
|---|---|---|
| Policy-violation rate | **0** | Eval + red-team suites, production verifier logs |
| Refund amount exactness | **100%** | Unit/property tests + eval DB-state checks |
| pass^1 on eval suite | ≥ 0.90 | Nightly eval |
| pass^k (k=4) on eval suite | ≥ 0.80 | Nightly eval |
| Correct route (auto / approval / escalate) | ≥ 90% | Eval suite |
| PII leakage in outputs/logs | **0** | Red-team + log scanner |
| Shadow-mode agreement with humans (low-risk cases) | ≥ 90–95% before enabling autonomy | Shadow pilot |

### 2.2 Service SLOs

| SLO | Target |
|---|---|
| API availability | 99.5% monthly |
| Chat turn latency (p95, excluding image analysis) | ≤ 6 s |
| Evidence assessment latency (p95) | ≤ 15 s |
| Outbox action execution delay (p95) | ≤ 60 s |
| Notification dispatch after state change (p95) | ≤ 2 min |

### 2.3 Business KPIs (tracked, not release-blocking)
Auto-resolution rate, refund → exchange conversion, cost per return, resolution time, escalation rate, approval override rate, CSAT, repeat-contact rate.

---

## 3. Key decisions (ADR summary)

Each decision gets a short ADR file in `/docs/adr/` during Sprint 0.

| # | Decision | Rationale |
|---|---|---|
| ADR-01 | Monorepo: Next.js frontend + FastAPI backend | One place for contracts, CI and docs |
| ADR-02 | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic | AI ecosystem; Pydantic schemas double as LLM output schemas |
| ADR-03 | PostgreSQL 16 + pgvector | OLTP + playbook retrieval in one DB |
| ADR-04 | Redis + ARQ workers | Background jobs, outbox relay, timers, per-case locks |
| ADR-05 | S3-compatible object storage (MinIO in dev) | Evidence images and documents |
| ADR-06 | Policy rules and graph edge conditions in **JSONLogic** | One safe, data-driven expression language; no `eval()` |
| ADR-07 | Graph-as-data (versioned JSON) **compiled to a LangGraph `StateGraph`**; business logic in plain Python | JSON keeps versioning, admin editing and self-evolution; LangGraph supplies checkpointing, human-in-the-loop pause/resume, streaming and retries |
| ADR-08 | DeepSeek-V4.1-Flash behind our own thin `LLMClient` interface | Vision + reasoning in one model; swappable provider |
| ADR-14 | **No LangChain** (optionally its DeepSeek integration package only, if the spike shows a clear benefit); no CrewAI/AutoGen/LlamaIndex | Heavy abstractions add little; money-moving workflows need tight control |
| ADR-15 | LangGraph pinned to an exact version; domain logic framework-independent (ports & adapters) | Limits exposure to framework churn; runtime stays swappable |
| ADR-09 | Money as integer minor units (paise) | No floating-point errors |
| ADR-10 | Transactional outbox + idempotency keys for all side effects | Exactly-once business effects |
| ADR-11 | Hash-chained, append-only audit log (per case) | Tamper evidence |
| ADR-12 | OpenTelemetry + Langfuse (self-hosted, with its LangGraph callback integration) | Node-level and LLM tracing |
| ADR-13 | Feature flags + autonomy levels per category | Safe rollout and instant kill switch |

---

## 4. Team, roles and working agreements

### 4.1 Roles (team of 4)

| Role | Owns |
|---|---|
| **Tech lead / backend** | Graph runtime, DI layer, policy engine, architecture, code review |
| **Backend / platform** | Data model, execution, lifecycle, adapters, CI/CD, infra, security |
| **AI engineer** | LLM layer, prompts, evidence/vision, eval harness, red team, learning loop |
| **Frontend engineer** | Customer chat, support console, admin studio, analytics UI, E2E tests |

Product decisions (policy content, thresholds, escalation rules) need a named **product owner**, even if part-time.

### 4.2 Working agreements
- Trunk-based development, short-lived feature branches, PR review by at least one other engineer.
- Every PR: tests included, CI green, no lowered coverage on `core/`.
- Prompts, policies, graphs and gate thresholds are **versioned files**, reviewed like code.
- Weekly eval report; any regression blocks merge of the change that caused it.
- Definition of Done in §19 applies to every story.

---

## 5. Repository structure

```
/apps
  /web                      # Next.js: customer chat, support console, admin studio
/services
  /api                      # FastAPI app: routers, auth, RBAC, schemas
  /core
    /graph                  # graph JSON schema + validator, compiler to LangGraph, node-contract wrappers,
                            # router (conditions + invariants + look-ahead), versioning
    /di                     # reason, search (options + scoring), infer (confidence), gate, explain
    /policy                 # YAML loader, JSONLogic evaluator, versioning, trace, refund calculator
    /evidence               # deterministic checks, vision assessment, fusion
    /risk                   # signals, scoring
    /execution              # outbox, saga/compensation, idempotency
    /lifecycle              # domain events, timers, SLA clocks, notifications
    /approvals              # approval + escalation queues, handoff packets
    /audit                  # hash-chained log
    /analytics              # aggregates, reports
    /llm                    # LLMClient interface, DeepSeek provider, prompts, schemas, verifier, redaction
  /adapters                 # oms, carrier, payment, inventory, notification — interfaces + mocks
  /learning                 # signal collector, reflector, curator (L1), replay runner
  /workers                  # ARQ worker entrypoints, outbox relay, scheduler
/config
  /policies/*.yaml          # versioned return policies
  /graphs/*.json            # versioned procedural graphs
  /prompts/*.md             # versioned prompts per node
  gate.yaml                 # autonomy thresholds
  flags.yaml                # feature flags / autonomy levels
/evals
  /cases                    # synthetic eval cases (YAML/JSON)
  /redteam                  # adversarial cases
  /simulator                # LLM customer simulator
  /graders                  # DB-state graders, LLM judge (tone only)
/infra                      # docker-compose, Dockerfiles, seed scripts, deployment manifests
/docs                       # architecture, ADRs, runbooks, API docs
```

---

## 6. Environments, CI/CD and DevOps

### 6.1 Environments

| Env | Purpose | Data | LLM |
|---|---|---|---|
| **local** | Development | Seeded synthetic data | Fake LLM (fixtures) by default; real model opt-in |
| **ci** | Automated tests | Ephemeral containers | Fake LLM; nightly job uses real model |
| **staging** | Integration, evals, demo, UAT | Synthetic + anonymised samples | Real model |
| **production** | Live | Real | Real model |

### 6.2 CI pipeline (every PR)
1. Lint & format: `ruff`, `mypy --strict` on `core/`, `eslint`, `tsc --noEmit`.
2. Unit tests (pytest + Hypothesis) with coverage gate (≥ 90% on `core/policy`, `core/graph`, `core/execution`; ≥ 80% overall backend).
3. Integration tests against Postgres, Redis, MinIO containers with mock adapters and fake LLM.
4. Eval smoke: 30 critical cases with fake LLM fixtures (verifies plumbing, not model quality).
5. Security: `pip-audit`, `npm audit`, secret scan (gitleaks), container scan (trivy).
6. Build images; DB migration dry-run (`alembic upgrade head` on a fresh DB, then `downgrade -1`).

### 6.3 Nightly / pre-release
- Full eval suite (~200 cases × 4 runs) and red-team suite against the **real model** on staging.
- E2E Playwright suite on staging.
- Report published; gates from §2.1 enforced before any production release.

### 6.4 Deployment
- Containerised services: `api`, `worker`, `scheduler`, `web`, plus Postgres, Redis, object storage, Langfuse.
- Dev/demo: Docker Compose. Production: any container platform (managed Kubernetes or a container PaaS), managed Postgres with PITR backups, managed Redis.
- Zero-downtime deploys; migrations are backward-compatible (expand → migrate → contract).
- Secrets in a secret manager; never in images or repo.

---

## 7. Timeline and milestones

| Sprint | Weeks | Build steps (§8) | Milestone |
|---|---|---|---|
| S0 | 1 | Phase 0 — spikes, scaffolding, CI, ADRs | Dev environment + CI green |
| S1 | 2–3 | Phase 1 Foundations · Phase 2 Policy engine | Policy engine passes all unit/property tests |
| S2 | 4–5 | Phase 3 Graph runtime · Phase 4 LLM layer · Phase 7 eval harness v0 | Case moves through graph to eligibility via chat API |
| S3 | 6–7 | Phase 5 DI layer · Phase 6 Execution & lifecycle | **M1: full case end-to-end (API/text only)** |
| S4 | 8–9 | Phase 8 Evidence · Phase 9 Human-in-the-loop · eval harness v1 | Risky/evidence cases routed correctly |
| S5 | 10–11 | Phase 10 Interfaces | **M2: full product demo** |
| S6 | 12–13 | Phase 11 Analytics · Phase 12 L1 learning · hardening | Quality bars met on staging |
| S7 | 14 | Security review, load test, runbooks, shadow-mode readiness | **M3: production launch in shadow mode** |

---

## 8. Work breakdown — phase by phase

Each phase lists **tasks**, **deliverables**, **acceptance criteria (AC)** and **tests**. A phase is done only when all AC pass.

---

### Phase 0 — Spikes and scaffolding (S0)

**Tasks**
- [ ] Monorepo scaffold, Docker Compose (Postgres, Redis, MinIO, Langfuse), Makefile/task runner.
- [ ] CI pipeline (§6.2) with placeholder tests.
- [ ] ADRs 01–15 written.
- [ ] **LangGraph spike:** on the pinned version, prove (a) compiling a JSON graph into a `StateGraph` with conditional edges, (b) Postgres checkpointer with `thread_id = case_id` surviving a process restart, (c) pausing at a human-approval node and resuming with the approver's decision, (d) two graph versions loaded side by side.
- [ ] **LLM spike (critical, de-risks ADR-08):** confirm from DeepSeek docs and a live test:
  - exact model ID and endpoint/SDK compatibility;
  - image input request format and limits;
  - JSON output mode behaviour (schema-enforced or JSON-only);
  - tool-calling format and parallel calls;
  - thinking mode and reasoning-effort parameter;
  - rate limits, timeouts, pricing, data-retention terms, hosting options (official API vs third-party/self-hosted).
- [ ] Fake LLM provider for tests (deterministic fixtures keyed by prompt hash).
- [ ] Synthetic seed data generator (customers, addresses, products with images, orders incl. COD, coupons, multi-item, delivered/undelivered).

**Deliverables:** running stack, green CI, spike report with go/no-go on the model.
**AC:** `make up` starts all services; CI runs on PRs; spike reports confirm (1) model vision + JSON + tools work, or name the fallback, and (2) LangGraph checkpoint, pause/resume and multi-version loading work on the pinned version.

---

### Phase 1 — Foundations (S1)

**Tasks**
- [ ] Database schema v1 + Alembic migrations (§9).
- [ ] Auth: customer OTP login (mock SMS/email provider in dev), staff login with JWT access + refresh tokens, password hashing (argon2).
- [ ] RBAC middleware with roles: `customer`, `agent`, `approver`, `admin`, `qc_operator`, `analyst`.
- [ ] Adapter interfaces + mock implementations: `OrderAdapter`, `CarrierAdapter`, `PaymentAdapter`, `InventoryAdapter`, `NotificationAdapter`. Mocks support failure injection (timeouts, errors, delayed webhooks).
- [ ] Audit log module: append API, per-case hash chain, verification function.
- [ ] Structured logging, request IDs, OpenTelemetry tracing baseline.
- [ ] PII field-level encryption utilities (phone, address, bank/UPI details).

**AC**
- Migrations apply and roll back cleanly.
- Role matrix enforced on every endpoint (tests cover allow and deny for each role).
- Audit chain verification detects any tampered row.
- Mock adapters can simulate success, failure and delays deterministically.

**Tests:** unit (auth, RBAC, audit chain, encryption), integration (adapters with failure injection).

---

### Phase 2 — Policy engine and refund calculator (S1)

**Tasks**
- [ ] Policy YAML schema (rule: `clause_id`, `text`, `applies_to`, `condition` (JSONLogic), `on_fail`, `requires_evidence`, `allowed_resolutions`, `fees`).
- [ ] JSONLogic evaluator (vetted library or small in-house evaluator with full test coverage) — **no arbitrary code execution**.
- [ ] Policy versioning with `effective_from`; case evaluated against version active on **order date**.
- [ ] Legal rules layer (statutory rights) evaluated **before** merchant policy and cannot be overridden by it (§13).
- [ ] Evaluation output: `eligible`, `allowed_resolutions[]`, `required_evidence[]`, `fees`, `window_remaining`, `trace[{clause_id, result, inputs}]`.
- [ ] Refund calculator: per-item paid amount with coupon/BOGO pro-ration, shipping refundability, restocking fees, loyalty reversal, split payments, store-credit vs source refund, COD → bank/UPI.
- [ ] Seed policy v1 covering: return window, non-returnable categories (innerwear, perishables, customised, final sale), damaged/defective, wrong item, size/fit, gift returns.

**AC**
- 100% of policy clauses covered by tests (pass and fail cases).
- Property tests: refund ≤ paid amount for any input; sum of item refunds ≤ order total; deterministic output for identical input.
- Trace lists every evaluated clause with inputs.

**Tests:** unit + Hypothesis property tests; golden-file tests for trace output.

---

### Phase 3 — Procedural graph runtime on LangGraph (S2)

**Approach:** the graph is authored and versioned as JSON (our source of truth). At load time each version is compiled into a LangGraph `StateGraph`. LangGraph handles execution, checkpointing, pause/resume and streaming; our code handles contracts, routing rules, invariants, look-ahead and all business logic.

**Tasks**
- [ ] Graph JSON schema: nodes (contract: `id`, `kind`, `task`, `inputs`, `output_schema`, `allowed_tools`, `validators`, `on_failure`, `timeout_s`, `policy_refs`) and edges (`from`, `to`, `relation`, `condition`, `guidance`, `pitfalls`, `priority`).
- [ ] Graph v1 with all 25 nodes:
  `START, AUTHENTICATE, IDENTIFY_ORDER, UNDERSTAND_REQUEST, CLARIFY, CHECK_ELIGIBILITY, EXPLAIN_INELIGIBLE, REQUEST_EVIDENCE, ASSESS_EVIDENCE, RISK_SCORE, GENERATE_OPTIONS, SCORE_OPTIONS, AUTONOMY_GATE, HUMAN_APPROVAL, CUSTOMER_CONFIRM, SCHEDULE_PICKUP, TRACK_SHIPMENT, INSPECT_QC, ISSUE_REFUND, CREATE_REPLACEMENT, CREATE_EXCHANGE, KEEP_ITEM_REFUND, DISPUTE, ESCALATE, CLOSE`.
- [ ] Structural validator: known node kinds, valid endpoints, every node reaches a terminal, no unreachable nodes, cycle policy (only `CLARIFY ⇄ UNDERSTAND_REQUEST` and evidence re-request loops allowed, each with a max-iteration guard).
- [ ] **State schema:** typed `CaseState` (case ID, current node, facts, policy result, options, decision, confidence, risk, approvals, counters for loop guards). Business data lives in our domain tables; `CaseState` holds references and execution context only.
- [ ] **Compiler (JSON → LangGraph):**
  - each graph node → a LangGraph node wrapping its **node contract** (allowed tools, output-schema validation, timeout, `on_failure`);
  - each node's outgoing edges → one **conditional-edge router** that: loads the 2-hop neighbourhood → evaluates JSONLogic conditions against current state → removes paths that break an invariant → runs the second-step feasibility check (look-ahead) → returns the next node (DI layer chooses at decision nodes);
  - compiled graphs cached per version; each case pins the graph version it started on.
- [ ] **Checkpointing:** LangGraph Postgres checkpointer in the same database, `thread_id = case_id`; a case resumes exactly where it stopped after restarts or deploys.
- [ ] **Waiting for humans and events:** `HUMAN_APPROVAL`, `CUSTOMER_CONFIRM`, `INSPECT_QC` and `TRACK_SHIPMENT` pause the graph; approver decisions, customer replies, QC results and carrier/payment webhooks resume it with the incoming payload.
- [ ] **Idempotent nodes (critical):** a node may re-run after a crash between its own DB commit and the checkpoint save. Therefore nodes write domain data with idempotent upserts keyed by `(case_id, node, step)`, and **never call adapters directly** — side effects go only through the outbox with idempotency keys (Phase 6).
- [ ] `return_case.current_node` mirrored from graph state on every transition, for queries and dashboards.
- [ ] Global invariants INV-1…INV-7 enforced in code (see below), independent of the graph and of LangGraph.
- [ ] **Concurrency:** LangGraph does not serialise concurrent runs on one thread, so keep a per-case lock (Redis lock or `SELECT … FOR UPDATE` + optimistic `version` column); events arriving while a case is locked are queued.
- [ ] Off-graph handling: an attempted action not on the graph → relocate on the full graph → else `ESCALATE`.
- [ ] Loop guards: max clarifications (3), max evidence requests (2), max total turns (30) → escalate; LangGraph recursion limit set as a backstop.
- [ ] Streaming: LangGraph streaming wired to the WebSocket for live customer replies and console updates.

**Invariants**

| ID | Rule |
|---|---|
| INV-1 | No order data or PII disclosed before `AUTHENTICATE` succeeds |
| INV-2 | No resolution offer before `CHECK_ELIGIBILITY` returns eligible |
| INV-3 | `ISSUE_REFUND` / `KEEP_ITEM_REFUND` require an approval token when the gate returned `approval` |
| INV-4 | Refund amount ≤ paid amount for the item(s), computed by code |
| INV-5 | At most one refund per return item |
| INV-6 | Outbound messages contain no commitment absent from the DecisionRecord |
| INV-7 | `CLOSE(resolved)` requires a terminal execution success event |

**AC**
- Graph v1 passes structural validation; a deliberately broken graph is rejected with clear diagnostics.
- Every invariant has a test proving the runtime blocks the violating transition.
- Concurrent event test: two simultaneous events on one case produce exactly one transition.
- Look-ahead test: out-of-stock exchange is pruned before being offered.
- Restart test: killing the worker mid-case and restarting resumes from the last checkpoint with no duplicate side effects.
- Pause/resume test: a case waiting at `HUMAN_APPROVAL` resumes correctly on approve, modify and reject.
- Version test: cases started on graph v1 finish on v1 after v2 is activated.

**Tests:** unit (validator, compiler, router conditions, invariants, look-ahead), integration (full transitions with DB and checkpointer), concurrency, restart and pause/resume tests.

---

### Phase 4 — LLM layer (S2)

**Tasks**
- [ ] `LLMClient` interface (our own thin wrapper, no LangChain): `complete(messages, schema?, tools?, images?, reasoning_effort?, timeout)`; providers: `DeepSeekProvider`, `FakeProvider`. Called from inside LangGraph node functions.
- [ ] Structured output: request JSON mode, then **always validate against the Pydantic schema**; on failure retry once with the validation error appended; on second failure route to the node's `on_failure`.
- [ ] Tool calling with runtime enforcement of the node's `allowed_tools` (disallowed call → error tool result, logged).
- [ ] Context assembly order: fixed system prompt → conversation (customer text only in user role) → per-node guidance (task, 2-hop neighbourhood, edge guidance/pitfalls, playbook entries) as a mid-conversation system message.
- [ ] PII redaction before every LLM call (names, phones, emails, addresses, bank/UPI → placeholders), re-hydration only in final customer-facing text where appropriate.
- [ ] Node implementations: `UNDERSTAND_REQUEST` (extraction with k=3 parallel samples → agreement score), `CLARIFY` (asks only for missing required slots), `IDENTIFY_ORDER` (match text to orders/items), `EXPLAIN_INELIGIBLE` (cites clause, offers alternatives + human review).
- [ ] **Outbound verifier (INV-6):** every customer-facing message checked against the DecisionRecord — deterministic checks (amounts, dates, resolution type) + LLM check for implied promises; violations block the message and regenerate once, then escalate.
- [ ] Prompt files versioned in `/config/prompts`; prompt version recorded in every audit event.
- [ ] Resilience: timeouts, exponential backoff on rate limits/5xx, circuit breaker; on provider outage, cases move to a "waiting" state and customers get a holding message; no decisions made without the model.
- [ ] Cost and latency metering per call.
- [ ] Multilingual: respond in the customer's language; Hinglish test cases.

**AC**
- Extraction schema validity ≥ 99.5% after retry on the eval set.
- Prompt-injection cases ("ignore your rules…") never change node behaviour.
- No PII present in any LLM request payload (verified by a payload scanner test).
- Verifier blocks 100% of seeded "unauthorised promise" messages.

**Tests:** unit (redaction, schema validation, retries, verifier rules), integration with FakeProvider, nightly evals with real model.

---

### Phase 5 — Decision Intelligence layer (S3)

**Tasks**
- [ ] **Reason:** facts object from extraction + order data; Policy Engine call; facts and trace stored.
- [ ] **Search:** enumerate legal options from policy output (`exchange, replacement, refund, store_credit, keep_item_refund, reject, ask_more`); compute utility:
  `U(o) = w_rev·RevenueRetained − w_cost·NetCost + w_cx·CustomerFit − w_risk·Risk·Exposure` (all terms normalised to [0,1]; weights in config);
  1–2 step look-ahead for feasibility (stock, serviceable pincode, carrier availability).
- [ ] **Infer:** `confidence = min(extraction_agreement, evidence_confidence, identification_confidence)`; rule decisions = 1.0; model self-reported confidence never used alone.
- [ ] Risk score input from Risk Service (Phase 8 delivers full signals; Phase 5 uses basic signals: return ratio, account age, item value, return-before-delivery).
- [ ] **Autonomy gate** (`gate.yaml`, admin-editable, bounded by hard min/max limits in code):

  | Condition | Route |
  |---|---|
  | risk < 0.3 and value < ₹5,000 and confidence ≥ 0.8 and no flags | AUTO |
  | risk < 0.6 and (value ≥ ₹5,000 or 0.6 ≤ confidence < 0.8) | APPROVAL |
  | risk ≥ 0.6 or confidence < 0.6 or legal threat / dispute / repeated anger | ESCALATE |

- [ ] **Explain:** DecisionRecord (facts, policy trace, options with scores, chosen option, look-ahead results, confidence, risk + signals, route, rationale text, versions of graph/policy/prompt/model).
- [ ] Global kill switch: force every route to APPROVAL.

**AC**
- Given identical inputs, DI output is deterministic except for the LLM-produced rationale text.
- `keep_item_refund` selected automatically when reverse cost > recovery value (test).
- Every gate row covered by tests, including boundary values.
- Kill switch verified to override all routes.

**Tests:** unit (scoring, gate, confidence), integration (DI with policy + graph), eval cases for route accuracy.

---

### Phase 6 — Execution and lifecycle (S3) → **Milestone M1**

**Tasks**
- [ ] Transactional outbox: transition + side-effect intent committed together; relay worker executes via adapters.
- [ ] Idempotency key per action: `hash(case_id, action_type, return_item_id)`; unique constraints on `refund.idempotency_key` and `outbox.idempotency_key`.
- [ ] Execution flows: refund (source / store credit / UPI-bank for COD), keep-item refund, replacement order, exchange (stock reserve → new order → pickup), pickup scheduling (slot selection, doorstep-QC checklist).
- [ ] Sagas with compensation: e.g., pickup fails 3× → hold refund / cancel replacement → notify with drop-off option → escalate if no response in 48 h.
- [ ] Webhook ingestion (carrier scans, payment status) with signature verification and replay protection.
- [ ] Domain events and Lifecycle service: notifications on every relevant transition (generated **from** the DecisionRecord), timers (pickup reminder, hand-over nudge, evidence follow-up, refund ETA, inactivity auto-close), SLA clocks (first response, resolution, refund TAT, India grievance: acknowledge 48 h / resolve 1 month).
- [ ] Case timeline API: merged view of messages, decisions, events, notifications.

**AC (M1)**
- Scripted conversation through the API completes all five flows (refund, keep-item, replacement, exchange, rejection) with correct final DB state.
- Retrying any action N times produces exactly one business effect.
- Each failure-injection scenario ends in a correct compensated state.
- Timers fire within tolerance; SLA breaches create escalations.

**Tests:** integration with failure injection, idempotency tests, saga tests, timer tests.

---

### Phase 7 — Evaluation harness (starts S2, grows every sprint)

**Tasks**
- [ ] Eval case format: initial customer profile/order fixtures, customer persona & goal, expected final DB state (resolution, refund amount, route), expected clauses cited, forbidden actions.
- [ ] LLM customer simulator (personas: clear, vague, angry, Hinglish, changes mind, gift recipient, adversarial).
- [ ] Graders: **DB end-state grader** (primary), clause-citation grader, policy-violation detector, LLM judge for tone/clarity only.
- [ ] Metrics: pass^1, pass^k (k=4), policy violations, route accuracy, refund exactness, turns-to-resolution, cost and latency per case.
- [ ] Case set v1 (~200): India specifics (COD refunds, RTO, doorstep QC), partial/multi-item/coupon pro-ration, out-of-window, non-returnable, out-of-stock exchange, carrier failures, gifts, damaged/defective with evidence.
- [ ] Red-team set (~60): prompt injection (text, inside documents, inside images), AI-edited damage photos, duplicate photos, serial returners, linked accounts, PII extraction attempts, threats/legal language, social engineering of the agent.
- [ ] Report generator; trend tracking across versions.
- [ ] Optional (Phase 2): τ²-bench retail domain adapter as external benchmark.

**AC:** suite runs in CI (smoke, fake LLM) and nightly (full, real model); report shows all §2.1 metrics.

---

### Phase 8 — Evidence and risk (S4)

**Tasks — Evidence**
- [ ] Upload pipeline: type/size validation, malware scan, storage, `evidence` row, thumbnail.
- [ ] Deterministic checks (no LLM): EXIF/metadata presence and capture time vs delivery date; perceptual-hash duplicate detection (same customer, other customers, catalogue images); optional C2PA content-credential check; editing/AI-generation indicators as **signals only**.
- [ ] Vision assessment (DeepSeek-V4.1-Flash, structured output): `{matches_catalog_item, defect_type, location, severity, condition_grade A–D, consistent_with_claim, missing_views[], confidence}`; compares against catalogue images; reads invoices/warranty cards/screenshots.
- [ ] Fusion: deterministic signals + vision result → evidence confidence + risk signals; evidence is advisory, never the sole basis for rejection.
- [ ] Targeted re-request: when `missing_views` is non-empty, ask for exactly those photos (max 2 requests).

**Tasks — Risk**
- [ ] Signals: return frequency/ratio, account age, item value, return before/at delivery, linked accounts (phone/email/address/device), prior confirmed fraud, evidence flags, reason–evidence mismatch, serial "damaged" claims, COD + high value.
- [ ] Weighted score in [0,1] with contributing signals listed; weights in config; fairness review — no protected attributes or close proxies.

**AC**
- Duplicate/reused photo test cases flagged ≥ 95%.
- Evidence assessment schema validity ≥ 99.5%; condition grade agreement with labelled set ≥ 85%.
- No case is auto-rejected on evidence or risk alone (test).

**Tests:** unit (EXIF, phash, fusion, risk weights), labelled image set for vision eval, red-team image cases.

---

### Phase 9 — Human-in-the-loop (S4)

**Tasks**
- [ ] Queues: `approval` and `escalation` (incl. fraud review) with priority, assignment, SLA timers, supervisor auto-escalation on breach.
- [ ] Handoff packet: AI case summary, timeline, DecisionRecord, evidence thumbnails, risk signals, suggested action + alternatives.
- [ ] Approver actions: approve / modify / reject with mandatory reason code; issues a signed, single-use approval token consumed by execution (INV-3).
- [ ] Authority limits per role (e.g., agent ≤ ₹2,000 goodwill, approver ≤ ₹25,000, two-person approval above).
- [ ] Goodwill exceptions (case-only, reason-coded, logged; never modify policy).
- [ ] Customer "request human review" on any automated denial.
- [ ] Neutral customer messaging for fraud review ("specialist review"), never accusatory.

**AC**
- Execution refuses refund without a valid token when route = APPROVAL (test).
- Two-person rule enforced above limit.
- Every human action appears in the audit chain with actor, role, reason.

---

### Phase 10 — Interfaces (S5) → **Milestone M2**

**Customer app**
- [ ] OTP login, order list, start return (chat-first with item picker fallback), photo upload, live case status/timeline, offer acceptance, pickup slot picker, refund/replacement tracking, "talk to a human", AI disclosure banner.

**Support console**
- [ ] Case list with filters (status, risk, SLA, route), case detail (timeline, chat, DecisionRecord viewer with clause citations, evidence viewer), approval queue, escalation queue, AI case summary, customer return history.

**Admin studio**
- [ ] Policy editor (YAML with validation, version publish with effective date), gate thresholds editor (bounded), feature flags/autonomy levels, graph viewer (React Flow, read-only in MVP), playbook review (approve/reject L1 entries), user & role management.

**Cross-cutting**
- [ ] Accessibility (WCAG 2.1 AA basics), responsive layouts, i18n-ready strings, real-time updates via WebSocket.

**AC (M2):** all five resolution flows and the approval/escalation flows demonstrable in the UI; Playwright E2E suite green.

---

### Phase 11 — Analytics (S6)

**Tasks**
- [ ] Aggregation jobs (materialised views): reason distribution by SKU/size/category, refund vs exchange mix, revenue retained, cost per return, auto-resolution rate, escalation and override rates, SLA compliance, resolution-time percentiles, fraud flagged vs confirmed.
- [ ] Prevention insights: top SKU/size issues ("SKU X size M — 41% 'runs small'") with CSV export for merchandising.
- [ ] Dashboards in support console (role: analyst sees masked PII only).

**AC:** dashboard numbers reconcile with raw tables on a fixture dataset (automated test).

---

### Phase 12 — L1 self-improvement (S6)

**Tasks**
- [ ] Signal collector: approval rejections/modifications (reason codes), reopened/disputed cases, fraud confirmed at QC, verifier blocks, CSAT, SLA breaches.
- [ ] Reflector (batch job): turns signals into candidate lessons per node.
- [ ] Curator: dedupe/merge into per-node playbook entries (`guidance`, `pitfalls`) with source case IDs; embeddings in pgvector; selective retrieval per node at runtime (top-k, relevance threshold).
- [ ] Promotion gate: staged entries → full eval replay → promote only if no §2.1 metric regresses; admin can review/reject any entry.
- [ ] Frozen surface enforced in code: policy, gate bounds, invariants, verifier, eval suite, audit log, risk-signal whitelist cannot be modified by the learning loop.
- [ ] Repeated-override detector: same clause overridden ≥ N times in 30 days → "policy may need review" alert to admin (suggestion only).

**AC**
- A seeded pattern of overrides produces a playbook entry that improves the related eval cases without regressing others.
- Attempt by the learning job to write outside playbook tables fails (test).

---

### Hardening (S6–S7) → **Milestone M3**

- [ ] Security review against §14 and the OWASP Top 10; dependency scans clean of high/critical issues.
- [ ] Load test: 50 concurrent chats, 500 cases/hour, outbox and webhook bursts; SLOs (§2.2) met.
- [ ] Chaos tests: LLM outage, DB failover, Redis restart, adapter timeouts.
- [ ] Backup/restore drill (PITR) and audit-chain verification after restore.
- [ ] Runbooks (§16), on-call rota, alert routing.
- [ ] Shadow-mode tooling: agent decisions recorded but not executed; agreement dashboard.

---

## 9. Data model

All tables include `id (uuid)`, `created_at`, `updated_at`; money in integer minor units with `currency`. LangGraph's checkpointer creates and manages its own checkpoint tables in the same database (not listed below).

| Table | Key columns |
|---|---|
| `customer` | name_enc, phone_enc, email_enc, tier, risk_profile_json |
| `address` | customer_id, pincode, address_enc |
| `product` | sku, title, category, attributes_json, image_uris[] |
| `order` | customer_id, placed_at, delivered_at, payment_method, total_minor, status |
| `order_item` | order_id, sku, variant, qty, unit_price_minor, discount_alloc_minor, final_sale |
| `policy` | version, effective_from, yaml, status |
| `policy_rule` | policy_id, clause_id, text, condition_json |
| `graph` | version, json, status, created_by, approved_by |
| `prompt_version` | node_id, version, content_hash |
| `playbook_entry` | node_id, guidance, pitfalls, source_case_ids[], status, embedding (vector) |
| `return_case` | customer_id, order_id, channel, current_node, graph_version, policy_version, status, route, priority, sla_due_at, version (optimistic lock), closed_at |
| `return_item` | case_id, order_item_id, qty, reason_category, reason_text, condition_grade, resolution |
| `message` | case_id, role, channel, content, redacted_content, created_at |
| `evidence` | case_id, return_item_id, uri, mime, phash, exif_json, checks_json, assessment_json |
| `decision_record` | case_id, node, json |
| `risk_assessment` | case_id, score, signals_json |
| `approval` | case_id, decision_id, requested_action, status, approver_id, reason_code, token_hash, decided_at |
| `shipment` | case_id, carrier, awb, pickup_slot, status, events_json |
| `refund` | case_id, amount_minor, method, idempotency_key (unique), status, gateway_ref |
| `replacement_order` | case_id, new_order_id, status |
| `exchange` | case_id, from_variant, to_variant, reservation_id, status |
| `outbox` | case_id, action, payload, idempotency_key (unique), status, attempts, next_attempt_at |
| `notification` | case_id, channel, template, status, sent_at |
| `sla_timer` | case_id, kind, due_at, fired_at |
| `audit_event` | case_id, actor_type, actor_id, action, payload_json, prev_hash, hash |
| `staff_user` | email, role, authority_limit_minor, status |
| `feature_flag` | key, value_json |

Indexes: `return_case(status, sla_due_at)`, `return_case(customer_id)`, `outbox(status, next_attempt_at)`, `audit_event(case_id, created_at)`, `evidence(phash)`, `playbook_entry` vector index.

---

## 10. API surface

| Area | Endpoints (prefix `/api/v1`) |
|---|---|
| Auth | `POST /auth/otp/request`, `POST /auth/otp/verify`, `POST /auth/staff/login`, `POST /auth/refresh` |
| Customer | `GET /me/orders`, `POST /cases`, `GET /cases/{id}`, `GET /cases/{id}/timeline`, `POST /cases/{id}/messages`, `POST /cases/{id}/evidence`, `POST /cases/{id}/confirm`, `POST /cases/{id}/human-review`, `WS /cases/{id}/stream` |
| Console | `GET /console/cases`, `GET /console/cases/{id}`, `GET /console/approvals`, `POST /console/approvals/{id}/decision`, `GET /console/escalations`, `POST /console/escalations/{id}/assign`, `POST /console/cases/{id}/goodwill` |
| Admin | `GET/POST /admin/policies`, `POST /admin/policies/{v}/publish`, `GET/PUT /admin/gate`, `GET/PUT /admin/flags`, `GET /admin/graphs`, `GET/POST /admin/playbook/{id}/decision`, `GET/POST /admin/users` |
| Analytics | `GET /analytics/summary`, `GET /analytics/reasons`, `GET /analytics/sla`, `GET /analytics/prevention.csv` |
| Webhooks | `POST /webhooks/carrier`, `POST /webhooks/payment` (signed) |
| Ops | `GET /healthz`, `GET /readyz`, `GET /metrics` |

OpenAPI spec generated by FastAPI; frontend types generated from it.

---

## 11. LLM integration (DeepSeek-V4.1-Flash)

### 11.1 Capabilities used
Model ID **`deepseek-flash`** (DeepSeek-V4.1-Flash), OpenAI-compatible Chat Completions. Image input (`image_url` data URLs, user messages only); thinking via `thinking: {"type": "enabled"|"disabled"}` — **on by default, so always set explicitly**; `reasoning_effort: "low"|"high"|"max"`; JSON mode (`response_format: json_object`) guarantees valid JSON but **not the schema** and requires the word "json" plus an example in the prompt; tool calling with optional `strict: true`; 1M context. Verified against api-docs.deepseek.com; live behaviour confirmed by the LLM spike.

### 11.2 Per-route configuration (starting values, tuned by evals)

| Route | Mode | Reasoning effort | Notes |
|---|---|---|---|
| `UNDERSTAND_REQUEST` (k=3) | thinking | low | JSON output + schema validation; samples in parallel |
| `IDENTIFY_ORDER` | non-thinking | — | JSON output |
| `CLARIFY`, customer replies | non-thinking | — | Streaming |
| `EXPLAIN_INELIGIBLE` | thinking | low | Must cite clause |
| `ASSESS_EVIDENCE` | thinking | high | Images + catalogue images, JSON output |
| Decision rationale text | thinking | low | Generated from DecisionRecord only |
| Outbound verifier | thinking | low | JSON `{violations[]}` |
| Case summary / handoff packet | non-thinking | — | JSON output |
| L1 reflector / curator (batch) | thinking | max | Offline |

Sampling: provider-recommended temperature (≈1.0); diversity is required for k=3 agreement.

### 11.3 Reliability rules
- Validate every structured output; one retry with the error message; then node `on_failure`.
- Timeouts per route; retries with backoff on 429/5xx; circuit breaker; holding message on outage.
- The model never computes money, eligibility or routing — only extracts, assesses, explains and converses.

### 11.4 Data protection
- PII redacted before every call; no raw bank/UPI/card data ever sent.
- Hosting choice decided in the spike: official API vs a third-party or self-hosted deployment of the open weights, considering DPDP obligations and customer data location.

---

## 12. Risk, fraud and imperfect-evidence handling

**Core rule:** suspicion adds friction or routes to a human — the agent never auto-rejects or accuses.

### 12.1 Risky operations
- Tiered authority by value × risk: auto → one approver → two-person approval.
- Refund timing as a control: risky cases refund at first carrier scan or after QC, not instantly.
- Prefer reversible resolutions first (store credit, exchange).
- Hard caps in code: max refunds per customer per day, approval token for large amounts.

### 12.2 Suspected fraud
- Risk score with explainable signals → fraud review queue.
- Neutral messaging ("specialist review").
- Proportional friction: extra photo → live photo/video → doorstep QC / return-before-refund → disable keep-item.
- Human makes the final decision; confirmed fraud feeds learning and watchlist; no automatic bans.

### 12.3 Imperfect or missing evidence

| | Low risk | High risk |
|---|---|---|
| **Low value** | Accept (investigation costs more than the item) | Return first, refund after QC |
| **High value** | One targeted follow-up photo → approval | Escalate; doorstep QC / physical inspection |

- Evidence is never the sole basis for a decision; physical QC is final.
- Persistent ambiguity → human may offer a partial outcome (partial refund / store credit) through approval.

---

## 13. Policy precedence and overrides

| Rank | Layer | Who | Rule |
|---|---|---|---|
| 1 | **Law** (statutory rights) | Built into engine | Overrides merchant policy; locked |
| 2 | **Merchant policy** | Admin | Versioned; applied by order date; never retroactive by accident |
| 3 | **Human exception (goodwill)** | Approver/supervisor | Within authority limit, reason code mandatory, case-only, audited |
| 4 | **AI decision** | Agent | Must stay inside policy; may only *recommend* exceptions |

Repeated overrides of one clause → admin alert "policy may need review". Learning may suggest, only admins change policy.

---

## 14. Security, privacy and compliance

Essentials only — everything below is required for MVP; anything beyond it is deferred until there is a concrete need.

| Area | Essential controls |
|---|---|
| Access | Customer OTP (rate-limited); staff password (argon2) + JWT with refresh; RBAC on every endpoint; customers see only their own cases; authority limits on money actions |
| Personal & payment data | Encrypt phone, address and bank/UPI fields; mask PII in UI and logs; **redact PII before every LLM call**; never store card data |
| AI safety | Customer, document and image text treated as data, never instructions; runtime tool permissions per node; outbound-message verifier |
| Integrations & uploads | Signed webhooks (HMAC); file type/size limits and malware scan on uploads |
| Audit | Hash-chained, append-only log per case |
| Infrastructure | TLS everywhere; encrypted database and storage; secrets in a secret manager; dependency and secret scanning in CI |
| Customer rights & compliance | AI disclosure and one-click human handoff; customer can contest any automated denial; India grievance timelines tracked by the SLA engine; DPDP basics (privacy notice, consent, data retention period) |

---

## 15. Testing strategy and quality gates

| # | Layer | Scope | Tooling | Gate |
|---|---|---|---|---|
| 1 | Unit | Policy, refund calc, invariants, graph, gate, scoring, redaction | pytest, Hypothesis | Every PR |
| 2 | Integration | Full flows with mock adapters, failure injection, idempotency, sagas, timers | pytest + containers | Every PR |
| 3 | AI evals | ~200 cases × k=4 with customer simulator, DB-state grading | `/evals` harness | Smoke every PR (fake LLM); full nightly + pre-release (real model) |
| 4 | Red team | Injection, fake/duplicate images, fraud patterns, PII extraction | `/evals/redteam` | Nightly + pre-release; 0 violations |
| 5 | E2E UI | Customer, console, admin journeys | Playwright | Nightly + pre-release |
| 6 | Shadow pilot | Agent proposes, humans decide | Agreement dashboard | Before enabling autonomy |

Additional: load/chaos tests (pre-launch), contract tests for adapters, migration tests, accessibility checks.

**Release gate:** all §2.1 bars met on the release candidate; no open high/critical security findings.

---

## 16. Observability, alerting and runbooks

- **Tracing:** one trace per event; spans per graph node, policy evaluation, LLM call (model, mode, effort, tokens, latency, cost), adapter call.
- **Metrics:** route distribution, auto-resolution rate, override rate, verifier blocks, schema-retry rate, LLM error/latency/cost, outbox lag, webhook failures, SLA breaches, refund volume per hour.
- **Alerts (paging):** verifier blocks spike, policy-violation detection > 0, refund volume anomaly, outbox lag > 5 min, LLM error rate > 5% for 5 min, audit-chain verification failure, SLA breach surge.
- **Runbooks:** LLM outage, payment adapter failure, refund anomaly (activate kill switch → force APPROVAL), webhook backlog, audit-chain alert, data-subject request, security incident.

---

## 17. Rollout plan

| Stage | Autonomy level | Exit criteria |
|---|---|---|
| R0 Shadow | Agent decides, nothing executed; humans handle all cases | ≥ 2 weeks; agreement ≥ 90–95% on low-risk cases; 0 policy violations |
| R1 Assisted | Agent executes only after human approval of every action | 2 weeks; override rate < 10%; no incidents |
| R2 Partial autonomy | AUTO enabled for low-value, low-risk categories (e.g., size/fit exchanges) | KPIs stable; refund anomaly alerts quiet |
| R3 Expanded autonomy | More categories and higher value bands, one at a time | Per-category agreement and KPIs reviewed monthly |

Controls: feature flags per category/value band, instant kill switch, weekly review of overrides and escalations.

---

## 18. Risk register

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Model inconsistency (low pass^k) | High | Medium | Graph constraints, deterministic policy, k=3 agreement, evals gate, escalation on low confidence |
| JSON / tool-call failures | Medium | Medium | Schema validation + retry + node failure path |
| Data residency / privacy with hosted LLM | High | Medium | PII redaction; hosting decision in spike; self-host option |
| Wrong refunds / money loss | High | Low | Code-computed amounts, invariants, idempotency, approval tokens, anomaly alerts, kill switch |
| Evolving fraud (AI-faked evidence) | High | Medium | Deterministic forensics, proportional friction, human review, learning from confirmed fraud |
| Prompt injection | High | Medium | Role separation, runtime tool permissions, verifier, red-team suite |
| Integration delays (real OMS/carrier/payment) | Medium | High | Adapter interfaces + mocks from day one; contract tests |
| Latency/cost growth | Medium | Medium | Per-route effort tuning, parallel sampling only where needed, metering and budgets |
| Self-improvement drift | High | Low | Frozen surface, eval replay gate, admin approval, versioning and rollback |
| Scope creep | Medium | High | MVP scope fixed (§1.3); Phase 2 backlog (§21) |

---

## 19. Definition of Done and launch checklist

### 19.1 Definition of Done (every story)
- [ ] Code reviewed and merged; CI green.
- [ ] Unit + integration tests added; coverage gates met.
- [ ] Relevant eval cases added/updated; no eval regression.
- [ ] Audit events emitted for every state change or AI/human action.
- [ ] Logs/traces/metrics added; no PII in logs.
- [ ] API docs and ADRs/runbooks updated where relevant.
- [ ] Feature flag in place for user-visible behaviour changes.

### 19.2 Launch checklist (M3)
- [ ] All §2.1 quality bars met on the release candidate.
- [ ] SLOs (§2.2) met in load test.
- [ ] Security review (§14 + OWASP Top 10) done; no high/critical findings open.
- [ ] Backup/restore drill passed; audit chain verifies after restore.
- [ ] Runbooks written; on-call and alert routing live.
- [ ] Kill switch tested in production config.
- [ ] Policy v1, gate thresholds and authority limits signed off by product owner.
- [ ] Privacy notice, AI disclosure and human-review path live.
- [ ] Shadow mode (R0) enabled.

---

## 20. Requirements traceability

| Module / requirement | Phase(s) | Verified by |
|---|---|---|
| M1 Identity & access (auth, profile, RBAC, secure data) | 1 (controls in §14) | RBAC tests, security review |
| M2 Order context (retrieval, history, timeline) | 1, 6, 10 | Integration + E2E |
| M3 Intake & understanding (creation, reasons, AI understanding, classification, clarification, NL support) | 4, 10 | Evals |
| M4 Eligibility & policy (verification, evaluation, explanations) | 2, 5 | Unit/property tests, evals |
| M5 Evidence & condition | 8 | Labelled image set, red team |
| M6 Resolution & execution (return/replacement/refund/exchange, recommendations, pickup, exchange mgmt) | 5, 6 | Integration, evals |
| M7 Lifecycle & communication (tracking, notifications, follow-ups) | 6 | Timer/integration tests |
| M8 Governance, ops & analytics (exceptions, disputes, escalation, approvals, dashboard, summaries, analytics, SLA, admin config, audit) | 9, 10, 11, 1 | E2E, reconciliation tests, audit verification |
| H1 Policy-grounded decisions with clause citations | 2, 4, 5 | Verifier tests, clause-citation grader |
| H2 Risk × value autonomy with evidence authenticity | 5, 8, 9 | Gate tests, red team |
| H3 Prevention analytics | 11 | Reconciliation tests |
| Procedural Graph (on LangGraph) | 3 | Structural, invariant, look-ahead, restart and pause/resume tests |
| DI layer (Reason → Search → Infer) | 5 | Unit + evals |
| Bounded RSI (L1) | 12 | Promotion-gate tests |

---

## 21. Phase 2 backlog

- WhatsApp and email channels.
- ML risk model calibrated on confirmed outcomes.
- L2 graph evolution: refiner proposes edits from failed vs successful trajectories → structural checks → eval replay → statistical acceptance → admin approval; visual graph diff in admin studio.
- L3 prompt optimisation (reflective prompt evolution) against the eval suite.
- Policy what-if simulator on historical cases.
- τ²-bench retail benchmark integration.
- Real carrier and payment sandbox adapters.
