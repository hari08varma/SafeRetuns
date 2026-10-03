DB_URL ?= postgresql://returns:returns@localhost:5432/returns
TEST_DB_URL ?= postgresql://returns:returns@localhost:5432/returns_test
STAFF_PASSWORD ?= change-me-please
RUN = uv run --directory services

.PHONY: install up down migrate seed-db api lint typecheck test test-all spike-graph spike-llm agent-smoke worker eval-smoke eval-full seed seed-demo create-admin web check

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

worker:        ## outbox relay + timers (run next to `make api`)
	DATABASE_URL=$(DB_URL) $(RUN) python -m returns_agent.workers.run

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

agent-smoke:   ## live LLM-layer check on DeepSeek (needs DEEPSEEK_API_KEY in services/.env)
	$(RUN) python spikes/agent_smoke.py

eval-smoke:    ## eval suite without a model (scripted customers); report in evals/reports
	DATABASE_URL=$(TEST_DB_URL) $(RUN) alembic upgrade head
	DATABASE_URL=$(TEST_DB_URL) $(RUN) python -m returns_agent.evals.run --mode smoke --strict --check-bars


eval-full:     ## DeepSeek agent + LLM customers, 4 trials (needs LLM_PROVIDER=deepseek + key in services/.env)
	DATABASE_URL=$(TEST_DB_URL) $(RUN) alembic upgrade head
	DATABASE_URL=$(TEST_DB_URL) $(RUN) python -m returns_agent.evals.run --mode full --check-bars

create-admin:  ## first admin for a real setup: make create-admin EMAIL=owner@yourstore.in
	DATABASE_URL=$(DB_URL) $(RUN) python -m returns_agent.seed.admin --email $(EMAIL)

seed-demo:     ## demo customers Priya (+91 90000 00001) and Rahul (+91 90000 00002), after seed-db
	DATABASE_URL=$(DB_URL) $(RUN) python -m returns_agent.seed.demo --staff-password $(STAFF_PASSWORD)

web:           ## web app on :3000 (API on :8000); set DEV_OTP_ECHO=true in services/.env to see OTPs
	cd apps/web && npm install && npm run dev


seed:
	$(RUN) python -m returns_agent.seed.generator --out ../seed.json

check: lint typecheck test-all
