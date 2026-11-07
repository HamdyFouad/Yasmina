"""Operational endpoints for probes, dashboard activity and aggregates."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from src.api.deps import get_ops_repository, get_worker_health_service, require_roles
from src.api.security import Principal, Role
from src.repositories.ops_repository import OpsRepository
from src.services.worker_metrics import WorkerHealthService

__all__ = ["api_router", "router"]

logger = logging.getLogger("yasmina.api.ops")

router = APIRouter(tags=["ops"])
api_router = APIRouter(prefix="/ops", tags=["ops"])


def _is_sales_only(principal: Principal) -> bool:
    return principal.has_any((Role.SALES,)) and not principal.has_any((Role.ADMIN, Role.VIEWER))


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


@api_router.get("/activity")
async def recent_activity(
    limit: int = Query(default=25, ge=1, le=100),
    repository: OpsRepository = Depends(get_ops_repository),
    _: Principal = Depends(require_roles(Role.ADMIN, Role.VIEWER)),
) -> dict[str, object]:
    """Recent hash-chained audit events for the operations overview.

    Restricted to admins and viewers because the audit stream is global and may
    reference tenders and actors outside a sales user's ownership scope.
    """

    events = await repository.recent_activity(limit=limit)
    return {"events": [event.as_dict() for event in events], "count": len(events)}


@api_router.get("/summary")
async def dashboard_summary(
    repository: OpsRepository = Depends(get_ops_repository),
    principal: Principal = Depends(require_roles(Role.ADMIN, Role.VIEWER, Role.SALES)),
) -> dict[str, object]:
    """Counts and monetary aggregates used by the dashboard KPI cards."""

    owner = principal.sub if _is_sales_only(principal) else None
    return await repository.summary(owner=owner)
