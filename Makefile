DB_URL ?= postgresql://returns:returns@localhost:5432/returns
TEST_DB_URL ?= postgresql://returns:returns@localhost:5432/returns_test
STAFF_PASSWORD ?= change-me-please
RUN = uv run --directory services

.PHONY: install up down migrate seed-db api lint typecheck test test-all spike-graph spike-llm seed check

install:
	cd services && uv sync

up:            ## Postgres (+ returns_test DB), Redis, MinIO
	docker compose up -d

down:
	docker compose down

migrate:
	DATABASE_URL=$(DB_URL) $(RUN) alembic upgrade head

seed-db:       ## loads seed data + demo staff <role>@saferetuns.dev
	DATABASE_URL=$(DB_URL) $(RUN) python -m returns_agent.seed.load --staff-password $(STAFF_PASSWORD)

api:           ## dev server on :8000 (needs services/.env with secrets)
	DATABASE_URL=$(DB_URL) $(RUN) uvicorn returns_agent.api.main:app --reload

lint:
	$(RUN) ruff check . && $(RUN) ruff format --check .

typecheck:
	$(RUN) mypy

test:          ## no database needed
	$(RUN) pytest -q -m "not postgres"

test-all:      ## full suite on the dedicated test database (reset on every run)
	DATABASE_URL=$(TEST_DB_URL) $(RUN) pytest -q

spike-graph:
	DATABASE_URL=$(TEST_DB_URL) $(RUN) python spikes/langgraph_spike.py

spike-llm:     ## needs DEEPSEEK_API_KEY in services/.env
	$(RUN) python spikes/llm_spike.py

seed:
	$(RUN) python -m returns_agent.seed.generator --out ../seed.json

check: lint typecheck test-all
