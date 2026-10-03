# Architecture Decision Records

Short form: decision, then why. Change a decision by adding a new ADR that supersedes it.

| # | Decision | Why |
|---|---|---|
| ADR-01 | Monorepo: Next.js frontend (`apps/web`) + Python backend (`services`) | One place for contracts, CI and docs |
| ADR-02 | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic | AI ecosystem; Pydantic schemas double as LLM output schemas |
| ADR-03 | PostgreSQL 16 + pgvector | OLTP and playbook retrieval in one database |
| ADR-04 | Redis + ARQ workers | Background jobs, outbox relay, timers, per-case locks |
| ADR-05 | S3-compatible object storage (MinIO in dev) | Evidence images and documents |
| ADR-06 | Policy rules and edge conditions in JSONLogic (small in-house evaluator) | One safe, data-driven expression language; no code execution |
| ADR-07 | Graph as versioned JSON, compiled to a LangGraph `StateGraph`; business logic in plain Python | JSON keeps versioning, editing and self-evolution; LangGraph gives checkpointing, pause/resume, streaming |
| ADR-08 | DeepSeek-V4.1-Flash behind our own thin `LLMClient` interface | Vision and reasoning in one model; provider swappable |
| ADR-09 | Money as integer minor units (paise) | No floating-point errors |
| ADR-10 | Transactional outbox + idempotency keys for every side effect | A retry can never repeat a refund |
| ADR-11 | Hash-chained, append-only audit log per case | Tamper evidence |
| ADR-12 | OpenTelemetry + Langfuse, added when LLM nodes land (Phase 4) | Node-level and LLM tracing |
| ADR-13 | Feature flags + autonomy levels per category | Safe rollout and a kill switch |
| ADR-14 | No LangChain, CrewAI, AutoGen or LlamaIndex | Heavy abstractions add little; money-moving flows need tight control |
| ADR-15 | LangGraph pinned to an exact version (1.2.12 + checkpoint-postgres 3.1.2); domain logic framework-independent | Limits framework churn; runtime stays swappable |
