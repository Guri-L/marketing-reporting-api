"""Application factory: wiring, middleware and error handling."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .cache import TTLCache
from .dependencies import InvalidRange
from .observability import configure_logging, request_id_var
from .routers import reporting
from .schemas import Health, Problem
from .security import AuthError, RateLimited, RateLimiter
from .service import NotFound, ReportingService
from .settings import Settings, get_settings
from .warehouse import Warehouse, WarehouseError, WarehouseTimeout, build_warehouse

log = logging.getLogger("marketing_api")

DESCRIPTION = """
Read-only API over marketing reporting tables in BigQuery: KPIs, campaign
leaderboards, channel and platform comparisons, and budget pacing.

Send your key in the `X-API-Key` header. All dates are `YYYY-MM-DD`. Errors use
the problem-details format with the request id in `instance`.
"""


def problem(
    status: int, title: str, detail: str | None = None,
    headers: dict[str, str] | None = None, errors: list[dict] | None = None,
) -> JSONResponse:
    body = Problem(title=title, status=status, detail=detail, instance=request_id_var.get(),
                   errors=errors)
    return JSONResponse(body.model_dump(exclude_none=True), status_code=status,
                        media_type="application/problem+json", headers=headers)


def create_app(settings: Settings | None = None, warehouse: Warehouse | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = warehouse is None
        active = warehouse or build_warehouse(settings)
        app.state.service = ReportingService(
            active, TTLCache(settings.cache_ttl_seconds, settings.cache_max_entries), settings)
        log.info("Started", extra={"fields": {"warehouse": settings.warehouse,
                                              "auth": not settings.auth_disabled}})
        if settings.auth_disabled:
            log.warning("Authentication is disabled. Do not expose this instance.")
        yield
        if owned:
            active.close()

    app = FastAPI(title="Marketing Reporting API", version=__version__,
                  description=DESCRIPTION, lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)

    if settings.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                           allow_methods=["GET"], allow_headers=["X-API-Key"])

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Accept a caller's id only if it is short and plain, so it is safe to log.
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if incoming.replace("-", "").isalnum() and len(incoming) <= 64 \
            else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("Unhandled error")
            response = problem(500, "Internal Server Error",
                               "Something went wrong. Quote the request id when reporting this.")
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        log.info("request", extra={"fields": {
            "method": request.method, "path": request.url.path,
            "status": response.status_code, "duration_ms": duration_ms,
            "client": getattr(request.state, "client", None),
            "cache": response.headers.get("X-Cache"),
        }})
        request_id_var.reset(token)
        return response

    @app.exception_handler(AuthError)
    async def _auth(request: Request, exc: AuthError) -> JSONResponse:
        return problem(401, "Unauthorized", str(exc), headers={"WWW-Authenticate": "ApiKey"})

    @app.exception_handler(RateLimited)
    async def _rate(request: Request, exc: RateLimited) -> JSONResponse:
        return problem(429, "Too Many Requests", f"Retry in {exc.retry_after} seconds.",
                       headers={"Retry-After": str(exc.retry_after)})

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return problem(404, "Not Found", str(exc))

    @app.exception_handler(InvalidRange)
    async def _range(request: Request, exc: InvalidRange) -> JSONResponse:
        return problem(422, "Invalid Parameters", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                   "message": e["msg"]} for e in exc.errors()]
        return problem(422, "Invalid Parameters", "One or more parameters are invalid.",
                       errors=errors)

    @app.exception_handler(WarehouseTimeout)
    async def _timeout(request: Request, exc: WarehouseTimeout) -> JSONResponse:
        return problem(504, "Gateway Timeout", "The warehouse did not answer in time.")

    @app.exception_handler(WarehouseError)
    async def _warehouse(request: Request, exc: WarehouseError) -> JSONResponse:
        # The cause is in the server log under this request id; clients get no internals.
        return problem(503, "Service Unavailable", "The warehouse is unavailable. Try again.",
                       headers={"Retry-After": "30"})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code, str(exc.detail))

    @app.get("/health", response_model=Health, tags=["Operations"],
             summary="Liveness: the process is running")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", response_model=Health, tags=["Operations"],
             summary="Readiness: the warehouse answers",
             responses={503: {"model": Problem}})
    def ready(request: Request) -> dict:
        service: ReportingService = request.app.state.service
        service.warehouse.query("data_range")  # deliberately not cached
        return {"status": "ready", "version": __version__}

    app.include_router(reporting.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn marketing_api.main:app_factory --factory``."""
    return create_app()
