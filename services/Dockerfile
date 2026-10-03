# syntax=docker/dockerfile:1
# Build from the repo root: docker build -f services/Dockerfile .
# Behind a TLS-inspecting proxy, pass its CA without baking it into the image:
#   docker build --secret id=ca_bundle,src=/path/to/ca.pem -f services/Dockerfile .
FROM python:3.11-slim

RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi && \
    pip install --no-cache-dir uv==0.8.17

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    CONFIG_DIR=/app/config

WORKDIR /app

# Dependencies first so code changes don't invalidate this layer.
COPY services/pyproject.toml services/uv.lock ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi && \
    uv sync --frozen --no-dev --no-install-project

COPY services/returns_agent ./returns_agent
COPY services/migrations ./migrations
COPY services/alembic.ini ./
COPY config ./config
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi && \
    uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/var/evidence && chown -R app /app/var
ENV EVIDENCE_DIR=/app/var/evidence
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn returns_agent.api.main:app --host 0.0.0.0 --port 8000"]
