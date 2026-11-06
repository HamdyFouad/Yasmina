"""Unauthenticated operational endpoints for probes and dashboards."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.api.deps import get_worker_health_service
from src.services.worker_metrics import WorkerHealthService

__all__ = ["router"]

logger = logging.getLogger("yasmina.api.ops")

router = APIRouter(tags=["ops"])


@router.get("/healthz/worker")
async def worker_health(
    service: WorkerHealthService = Depends(get_worker_health_service),
) -> JSONResponse:
    """Return the latest scrape/ingestion history and outbox queue state.

    A degraded worker still returns HTTP 200 so a transient failed run does not
    restart the API; a database/query failure returns 503.
    """

    try:
        snapshot = await service.snapshot()
    except Exception:
        logger.exception("worker health snapshot failed")
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "detail": "worker metrics are unavailable"},
        )
    return JSONResponse(status_code=200, content=snapshot)
