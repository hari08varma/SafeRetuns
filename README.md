# Returns Agent

Autonomous product return resolution agent: a procedural graph (versioned JSON, run on LangGraph),
a decision-intelligence layer, and bounded self-improvement. See the implementation plan.

## Status: Phase 0 complete (except the live DeepSeek check)

| Item | State |
|---|---|
| Project scaffold, Docker Compose (Postgres+pgvector, Redis, MinIO), Makefile, CI | Done |
| ADRs 01–15 | `docs/adr/README.md` |
| LLM layer: `LLMClient`, structured output with validation + retry, `FakeProvider`, `DeepSeekProvider` | Done, unit-tested |
| LangGraph spike: compile JSON graph, Postgres checkpoint across a real restart, pause/resume, versions side by side, idempotent refund | **GO** — `docs/spike-reports/langgraph.md` |
| LLM spike against DeepSeek-V4.1-Flash | Script ready; **not run yet** — `docs/spike-reports/llm.md` |
| Synthetic seed data generator | Done, unit-tested |

## Quick start

Requires Docker, [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
make install        # Python deps
make up             # Postgres, Redis, MinIO
make check          # lint + type check + all tests (incl. Postgres)
make spike-graph    # re-run LangGraph spike
make seed           # writes seed.json
cp .env.example services/.env && make spike-llm   # needs DEEPSEEK_API_KEY
```

## Layout

```
services/
  returns_agent/
    llm/        client interface, structured output, fake + DeepSeek providers
    graph/      graph JSON schema + validation, JSONLogic conditions, LangGraph compiler
    seed/       synthetic data generator
  spikes/       langgraph_spike.py, llm_spike.py
  tests/
config/graphs/  versioned graph JSON (spike_v1, spike_v2)
docs/           ADRs, spike reports
apps/web/       Next.js app (Phase 10)
```
