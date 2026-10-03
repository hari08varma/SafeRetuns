# Returns Agent

Autonomous product return resolution agent: a procedural graph (versioned JSON, run on LangGraph),
a decision-intelligence layer, and bounded self-improvement. See the implementation plan.

## Status

| Phase | State |
|---|---|
| 0 — scaffold, LLM layer, LangGraph spike (GO), seed generator | Done (live DeepSeek check: run `make spike-llm`) |
| 1 — foundations | **Done**: schema v1 (27 tables, Alembic), customer OTP + staff login (argon2, JWT access/refresh), RBAC with an enforced role matrix, PII encryption + blind indexes, hash-chained audit log, mock adapters with failure injection, seed loader, request-ID JSON logging |
| 2 — policy engine & refunds | **Done**: versioned YAML policies (legal layer locked, merchant layer by order date), deterministic evaluation with full clause trace, refund calculator in paise (coupon pro-ration, partial returns, fees, shipping, split/COD payments), property-tested |
| 3 — graph runtime on LangGraph | **Done**: graph `returns-v1` (25 nodes, node contracts, 8 waiting nodes), next-step choice with conditions → loop guards → hard invariants (INV-1..5, 7) → look-ahead, fallback to ESCALATE, case runner with per-case lock, typed events, Postgres checkpoints, version pinning and audited transitions. LLM/decision/execution nodes are labelled stubs until Phases 4–6 |

## Quick start (laptop with Docker)

```bash
cp .env.example services/.env   # fill JWT_SECRET, PII_ENCRYPTION_KEY, PII_INDEX_KEY (commands inside)
make install && make up          # deps; Postgres (+ returns_test), Redis, MinIO
make migrate && make seed-db STAFF_PASSWORD='<12+ chars>'
make api                         # http://localhost:8000/docs
make check                       # lint + types + all tests (uses returns_test, reset each run)
docker compose up --build        # or run everything, including the API, in Docker
make spike-llm                   # live DeepSeek check (DEEPSEEK_API_KEY in services/.env)
```

Demo staff logins: `<role>@saferetuns.dev` (agent, approver, admin, qc_operator, analyst).

## Layout

```
services/
  returns_agent/
    api/        FastAPI app, auth + RBAC dependencies, routes
    adapters/   external-system interfaces + mocks with failure injection
    audit/      hash-chained audit log
    db/         SQLAlchemy models, session
    security/   tokens, passwords, OTP, PII encryption
    llm/        client interface, structured output, fake + DeepSeek providers
    graph/      graph schema, compiler, invariants, look-ahead, nodes, registry, runner, events
    policy/     policy schema, evaluation engine, refund calculator
    seed/       synthetic data generator + DB loader
  migrations/   Alembic
  spikes/       langgraph_spike.py, llm_spike.py
  tests/
config/graphs/  versioned graph JSON (returns_v1; spike_v1/v2 are test fixtures)
config/policies/ legal + merchant return policies (YAML)
docs/           ADRs, spike reports
apps/web/       Next.js app (Phase 10)
```
