import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response

from returns_agent.api.deps import Adapters
from returns_agent.api.routes import router

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("returns_agent.http")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "request_id": getattr(record, "request_id", request_id_var.get()),
            }
        )


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def create_app(adapters: Adapters) -> FastAPI:
    app = FastAPI(title="SafeReturns API", version="0.1.0")
    app.state.adapters = adapters
    app.include_router(router)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            # Path and status only: never log bodies, which may contain PII.
            logger.info(
                "%s %s %s %.0fms",
                request.method,
                request.url.path,
                response.status_code,
                (time.perf_counter() - start) * 1000,
                extra={"request_id": rid},
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
