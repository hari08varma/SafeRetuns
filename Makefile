DB_URL ?= postgresql://returns:returns@localhost:5432/returns
TEST_DB_URL ?= postgresql://returns:returns@localhost:5432/returns_test
STAFF_PASSWORD ?= change-me-please
RUN = uv run --directory services
E2E_DB_URL ?= $(TEST_DB_URL)
E2E_PASSWORD ?= e2e-staff-password-123
# Same throwaway secrets for seeding and for the servers Playwright starts (apps/web/playwright.config.ts).
E2E_ENV = DATABASE_URL=$(E2E_DB_URL) JWT_SECRET=e2e-only-secret-xxxxxxxxxxxxxxxxxxxxxxxxxx \
	PII_ENCRYPTION_KEY=ZTJlLW9ubHkta2V5LWZvci1sb2NhbC10ZXN0cy0wMDA= PII_INDEX_KEY=e2e-index-key

.PHONY: install up down migrate seed-db api lint typecheck test test-all spike-graph spike-llm agent-smoke worker eval-smoke eval-demo eval-full seed seed-demo create-admin web e2e check

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

eval-demo:     ## 20 key cases on DeepSeek, 1 conversation each (~5 min, small credit use)
	DATABASE_URL=$(TEST_DB_URL) $(RUN) alembic upgrade head
	DATABASE_URL=$(TEST_DB_URL) $(RUN) python -m returns_agent.evals.run --mode full --demo --check-bars

eval-full:     ## DeepSeek agent + LLM customers, 4 trials (needs LLM_PROVIDER=deepseek + key in services/.env)
	DATABASE_URL=$(TEST_DB_URL) $(RUN) alembic upgrade head
	DATABASE_URL=$(TEST_DB_URL) $(RUN) python -m returns_agent.evals.run --mode full --check-bars

create-admin:  ## first admin for a real setup: make create-admin EMAIL=owner@yourstore.in
	DATABASE_URL=$(DB_URL) $(RUN) python -m returns_agent.seed.admin --email $(EMAIL)

seed-demo:     ## demo customers Priya (+91 90000 00001) and Rahul (+91 90000 00002), after seed-db
	DATABASE_URL=$(DB_URL) $(RUN) python -m returns_agent.seed.demo --staff-password $(STAFF_PASSWORD)

web:           ## web app on :3000 (API on :8000); set DEV_OTP_ECHO=true in services/.env to see OTPs
	cd apps/web && npm install && npm run dev

e2e:           ## browser tests: resets the test database, seeds demo data, runs Playwright
	$(E2E_ENV) $(RUN) alembic downgrade base
	$(E2E_ENV) $(RUN) alembic upgrade head
	$(E2E_ENV) $(RUN) python -m returns_agent.seed.load --staff-password $(E2E_PASSWORD)
	$(E2E_ENV) $(RUN) python -m returns_agent.seed.demo --staff-password $(E2E_PASSWORD)
	cd apps/web && npm ci && npm run build && E2E_DATABASE_URL=$(E2E_DB_URL) npx playwright test

seed:
	$(RUN) python -m returns_agent.seed.generator --out ../seed.json

check: lint typecheck test-all
