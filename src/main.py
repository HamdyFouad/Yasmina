"""FastAPI entry point for Project Yasmina.

Every startup runs the preflight checks first. Configuration, PostgreSQL,
pgvector schema, MinIO authentication/bucket access and ClamAV must all be
healthy before the application starts serving traffic. The optional n8n
automation service only produces a warning.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from src.api.metrics import MetricsMiddleware, render_metrics
from src.api.observability import RequestContextMiddleware
from src.api.rate_limit import RateLimitMiddleware
from src.api.routes import approvals, auth, integrations, invoices, offers, ops, tenders
from src.api.security import validate_security_configuration
from src.config import Settings, ensure_env_file, get_settings, reset_settings_cache
from src.db import dispose_engine
from src.logging_config import configure_logging
from src.preflight import CheckResult, PreflightOptions, run_preflight

__all__ = ["app", "create_app"]

logger = logging.getLogger("yasmina.main")


def _failures(checks: list[CheckResult]) -> list[CheckResult]:
    return [check for check in checks if check.status == "fail"]


def create_app(settings: Settings | None = None, *, run_startup_checks: bool = True) -> FastAPI:
    """Create the FastAPI application.

    ``settings`` is injectable for tests; production uses ``get_settings()``.
    ``run_startup_checks=False`` skips external preflight for mocked API tests.
    """

    @asynccontextmanager
    async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings is not None:
            current = settings
        else:
            if ensure_env_file():
                reset_settings_cache()
                logger.info("created .env from .env.example")
            current = get_settings()

        app.state.settings = current
        configure_logging(current.log_level, json_format=current.log_format == "json")
        validate_security_configuration(current)
        if run_startup_checks:
            checks = await run_preflight(
                current,
                PreflightOptions(timeout=current.preflight_timeout_seconds),
            )
            failures = _failures(checks)
            if failures:
                details = "; ".join(f"{check.name}: {check.detail}" for check in failures)
                raise RuntimeError(f"Startup preflight failed: {details}")
            app.state.preflight = tuple(checks)
            logger.info("preflight passed (env=%s)", current.app_env)
        else:
            app.state.preflight = ()
        try:
            yield
        finally:
            limiter = getattr(app.state, "rate_limiter", None)
            close = getattr(limiter, "aclose", None)
            if close is not None:
                await close()
            await dispose_engine()

    application = FastAPI(
        title="Project Yasmina",
        description="B2B sourcing, pricing and tender-bidding engine for German hygiene supplies.",
        version="0.1.0",
        lifespan=app_lifespan,
    )
    middleware_settings = settings if settings is not None else get_settings()
    if middleware_settings.rate_limit_enabled:
        application.add_middleware(
            RateLimitMiddleware,
            settings=middleware_settings,
        )
    application.add_middleware(MetricsMiddleware, enabled=middleware_settings.metrics_enabled)
    application.add_middleware(
        RequestContextMiddleware,
        trust_proxy_headers=middleware_settings.trust_proxy_headers,
    )
    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(tenders.router, prefix="/api/v1")
    application.include_router(approvals.router, prefix="/api/v1")
    application.include_router(offers.router, prefix="/api/v1")
    application.include_router(invoices.router, prefix="/api/v1")
    application.include_router(integrations.router, prefix="/api/v1")
    application.include_router(ops.router)

    @application.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if middleware_settings.metrics_enabled:

        @application.get("/metrics", include_in_schema=False)
        async def metrics() -> Response:
            payload, content_type = render_metrics()
            return Response(content=payload, media_type=content_type)

    @application.get("/readyz", tags=["ops"])
    async def readyz(request: Request) -> JSONResponse:
        checks = tuple(getattr(request.app.state, "preflight", ()))
        failures = _failures(list(checks))
        return JSONResponse(
            status_code=503 if failures else 200,
            content={
                "status": "unready" if failures else "ready",
                "checks": [check.to_dict() for check in checks],
            },
        )

    return application


app = create_app()
