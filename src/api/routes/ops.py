"""Operational endpoints for probes, dashboard activity and aggregates."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from src.api.deps import (
    get_audit_service,
    get_ops_repository,
    get_quotation_sync_repository,
    get_worker_health_service,
    require_roles,
)
from src.api.security import Principal, Role
from src.models.enums import AuditEventType
from src.repositories.ops_repository import OpsRepository
from src.repositories.quotation_sync_repository import (
    QuotationSyncJobRepository,
    QuotationSyncJobView,
    QuotationSyncRepositoryError,
)
from src.services.audit_service import AuditService
from src.services.worker_metrics import WorkerHealthService

__all__ = ["api_router", "router"]

logger = logging.getLogger("yasmina.api.ops")

router = APIRouter(tags=["ops"])
api_router = APIRouter(prefix="/ops", tags=["ops"])

OutboxJobStatus = Literal["pending", "processing", "succeeded", "failed", "skipped"]


def _job_payload(job: QuotationSyncJobView) -> dict[str, object]:
    return {
        "id": str(job.id),
        "offer_id": str(job.offer_id),
        "offer_number": job.offer_number,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "available_at": job.available_at.isoformat(),
        "quotation_name": job.quotation_name,
        "last_error": job.last_error,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


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


@api_router.get("/outbox")
async def list_outbox_jobs(
    status_filter: OutboxJobStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
    repository: QuotationSyncJobRepository = Depends(get_quotation_sync_repository),
    _: Principal = Depends(require_roles(Role.ADMIN, Role.APPROVER, Role.VIEWER)),
) -> dict[str, object]:
    """Paginated ERPNext quotation outbox with the last failure per job."""

    jobs, total = await repository.list_jobs(status=status_filter, limit=limit, offset=offset)
    return {
        "jobs": [_job_payload(job) for job in jobs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@api_router.post("/outbox/{job_id}/retry")
async def retry_outbox_job(
    job_id: str,
    repository: QuotationSyncJobRepository = Depends(get_quotation_sync_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN, Role.APPROVER)),
) -> dict[str, object]:
    """Reset a terminal failed quotation job to pending with zero attempts."""

    try:
        parsed = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="quotation sync job does not exist",
        ) from exc

    current = await repository.get(parsed)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="quotation sync job does not exist",
        )

    try:
        retried = await repository.retry_failed(parsed)
    except QuotationSyncRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    logger.info(
        "outbox job retried job_id=%s offer_number=%s actor=%s",
        retried.id,
        retried.offer_number,
        principal.sub,
    )
    await audit.append(
        event_type=AuditEventType.SYSTEM_OUTBOX_RETRIED,
        actor=principal.sub,
        entity_type="quotation_sync_job",
        entity_id=str(retried.id),
        payload={
            "offer_number": retried.offer_number,
            "previous_status": current.status,
            "previous_attempts": current.attempts,
            "previous_error": current.last_error,
        },
    )
    return {"job": _job_payload(retried)}


@api_router.get("/outbox-status")
async def outbox_status(
    repository: QuotationSyncJobRepository = Depends(get_quotation_sync_repository),
    _: Principal = Depends(require_roles(Role.ADMIN, Role.VIEWER)),
) -> dict[str, object]:
    """ERPNext quotation outbox counts for the operations dashboard."""

    counts = await repository.status_counts()
    return {
        "quotation_sync": counts,
        "total": sum(counts.values()),
    }
