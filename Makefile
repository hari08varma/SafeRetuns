DB_URL ?= postgresql://returns:returns@localhost:5432/returns
RUN = uv run --directory services

.PHONY: install up down lint typecheck test test-all spike-graph spike-llm seed check

install:
	cd services && uv sync

up:
	docker compose up -d

down:
	docker compose down

lint:
	$(RUN) ruff check . && $(RUN) ruff format --check .

typecheck:
	$(RUN) mypy

test:            ## unit tests, no services needed
	$(RUN) pytest -q -m "not postgres"

test-all:        ## includes Postgres checkpoint tests (needs `make up`)
	DATABASE_URL=$(DB_URL) $(RUN) pytest -q

spike-graph:     ## LangGraph spike -> docs/spike-reports/langgraph.md
	DATABASE_URL=$(DB_URL) $(RUN) python spikes/langgraph_spike.py

spike-llm:       ## DeepSeek spike -> docs/spike-reports/llm.md (needs DEEPSEEK_API_KEY)
	$(RUN) python spikes/llm_spike.py

seed:
	$(RUN) python -m returns_agent.seed.generator --out ../seed.json

check: lint typecheck test-all
