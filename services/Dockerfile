# Build from the repo root: docker build -f services/Dockerfile .
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    CONFIG_DIR=/app/config

WORKDIR /app

# Dependencies first so code changes don't invalidate this layer.
COPY services/pyproject.toml services/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/returns_agent ./returns_agent
COPY services/migrations ./migrations
COPY services/alembic.ini ./
COPY config ./config
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/var/evidence && chown -R app /app/var
ENV EVIDENCE_DIR=/app/var/evidence
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn returns_agent.api.main:app --host 0.0.0.0 --port 8000"]
