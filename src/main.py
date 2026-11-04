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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.config import Settings, ensure_env_file, get_settings, reset_settings_cache
from src.preflight import CheckResult, PreflightOptions, run_preflight

__all__ = ["app", "create_app"]

logger = logging.getLogger("yasmina.main")


def _failures(checks: list[CheckResult]) -> list[CheckResult]:
    return [check for check in checks if check.status == "fail"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application.

    ``settings`` is injectable for tests; production uses ``get_settings()``.
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

        checks = await run_preflight(
            current,
            PreflightOptions(timeout=current.preflight_timeout_seconds),
        )
        failures = _failures(checks)
        if failures:
            details = "; ".join(f"{check.name}: {check.detail}" for check in failures)
            raise RuntimeError(f"Startup preflight failed: {details}")

        app.state.settings = current
        app.state.preflight = tuple(checks)
        logger.info("preflight passed (env=%s)", current.app_env)
        yield

    application = FastAPI(
        title="Project Yasmina",
        description="B2B sourcing, pricing and tender-bidding engine for German hygiene supplies.",
        version="0.1.0",
        lifespan=app_lifespan,
    )

    @application.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

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
