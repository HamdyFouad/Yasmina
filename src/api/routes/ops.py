"""Operational endpoints for probes, dashboard activity and aggregates."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from src.api.deps import (
    get_app_settings,
    get_audit_service,
    get_ingestion_job_repository,
    get_ops_repository,
    get_quotation_sync_repository,
    get_worker_command_launcher,
    get_worker_command_repository,
    get_worker_health_service,
    get_worker_run_repository,
    require_roles,
)
from src.api.schemas import (
    IngestionDiscardPayload,
    ReleaseStaleLocksPayload,
    WorkerCommandCancelPayload,
    WorkerIngestionTriggerPayload,
    WorkerScrapeTriggerPayload,
)
from src.api.security import Principal, Role
from src.config import Settings
from src.models.enums import AuditEventType
from src.repositories.ingestion_repository import (
    IngestionJobView,
    IngestionRepositoryError,
    TenderIngestionJobRepository,
)
from src.repositories.ops_repository import OpsRepository
from src.repositories.quotation_sync_repository import (
    QuotationSyncJobRepository,
    QuotationSyncJobView,
    QuotationSyncRepositoryError,
)
from src.repositories.worker_command_repository import (
    WorkerCommandConflict,
    WorkerCommandNotFound,
    WorkerCommandRepository,
)
from src.repositories.worker_run_repository import WorkerRunRepository
from src.scrapers.portal_config import PortalConfigError, load_portal_targets
from src.scrapers.probe import probe_portal
from src.services.audit_service import AuditService
from src.services.worker_command import WorkerCommandError, WorkerCommandLauncher
from src.services.worker_metrics import WorkerHealthService

__all__ = ["api_router", "router"]

logger = logging.getLogger("yasmina.api.ops")

router = APIRouter(tags=["ops"])
api_router = APIRouter(prefix="/ops", tags=["ops"])

OutboxJobStatus = Literal["pending", "processing", "succeeded", "failed", "skipped"]
IngestionJobStatus = Literal["pending", "processing", "completed", "failed", "skipped"]
WorkerRunType = Literal["scrape", "ingestion"]
WorkerRunStatus = Literal["running", "succeeded", "failed", "skipped", "cancelled"]
WorkerCommandStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


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


def _ingestion_payload(job: IngestionJobView) -> dict[str, object]:
    return {
        "id": str(job.id),
        "tender_id": str(job.tender_id),
        "tender_number": job.tender_number,
        "tender_title": job.tender_title,
        "portal_name": job.portal_name,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "quarantined": job.quarantined,
        "quarantine_reason": job.quarantine_reason,
        "last_error": job.last_error,
        "attachment_count": len(job.attachments),
        "available_at": job.available_at.isoformat() if job.available_at is not None else None,
        "locked_at": job.locked_at.isoformat() if job.locked_at is not None else None,
        "created_at": job.created_at.isoformat() if job.created_at is not None else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at is not None else None,
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


async def _worker_runs_payload(
    runs: WorkerRunRepository,
    *,
    limit: int,
    offset: int,
    run_type: WorkerRunType | None,
    run_status: WorkerRunStatus | None,
) -> dict[str, object]:
    rows = await runs.list_runs(
        limit=limit,
        offset=offset,
        run_type=run_type,
        run_status=run_status,
    )
    total = await runs.count_runs(run_type=run_type, run_status=run_status)
    return {
        "runs": [run.as_dict() for run in rows],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@api_router.get("/worker-runs")
async def list_worker_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    run_type: WorkerRunType | None = Query(default=None),
    run_status: WorkerRunStatus | None = Query(default=None, alias="status"),
    runs: WorkerRunRepository = Depends(get_worker_run_repository),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Admin view of recent scrape/ingestion worker runs."""

    return await _worker_runs_payload(
        runs,
        limit=limit,
        offset=offset,
        run_type=run_type,
        run_status=run_status,
    )


@router.get("/healthz/worker_runs")
async def worker_runs_health_alias(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    run_type: WorkerRunType | None = Query(default=None),
    run_status: WorkerRunStatus | None = Query(default=None, alias="status"),
    runs: WorkerRunRepository = Depends(get_worker_run_repository),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Admin compatibility alias for the paginated worker-run list.

    The legacy ``/healthz/worker`` snapshot remains public because uptime probes
    depend on it. Full run rows stay admin-scoped here, just like
    ``/ops/worker-runs``.
    """

    return await _worker_runs_payload(
        runs,
        limit=limit,
        offset=offset,
        run_type=run_type,
        run_status=run_status,
    )


@api_router.get("/ingestion")
async def list_ingestion_jobs(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    status_filter: IngestionJobStatus | None = Query(default=None, alias="status"),
    quarantined: bool | None = Query(default=None),
    queue: bool | None = Query(default=None),
    repository: TenderIngestionJobRepository = Depends(get_ingestion_job_repository),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Admin queue, quarantine and dead-letter view for ingestion jobs."""

    jobs = await repository.list_jobs(
        status_filter=status_filter,
        quarantined=quarantined,
        queue=queue,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_jobs(
        status_filter=status_filter,
        quarantined=quarantined,
        queue=queue,
    )
    return {
        "jobs": [_ingestion_payload(job) for job in jobs],
        "count": total,
        "limit": limit,
        "offset": offset,
        "status_counts": await repository.status_counts(),
        "quarantine_count": await repository.quarantine_count(),
    }


@api_router.post("/ingestion/{job_id}/retry")
async def retry_ingestion_job(
    job_id: UUID,
    repository: TenderIngestionJobRepository = Depends(get_ingestion_job_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Requeue a failed/dead-letter ingestion job with a fresh attempt budget."""

    try:
        job = await repository.retry_failed(job_id)
    except IngestionRepositoryError as exc:
        message = str(exc)
        code = status.HTTP_404_NOT_FOUND if "does not exist" in message else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=message) from exc

    await audit.append(
        event_type=AuditEventType.SYSTEM_INGESTION_RETRIED,
        actor=principal.sub,
        entity_type="tender_ingestion_job",
        entity_id=str(job.id),
        payload={
            "tender_id": str(job.tender_id),
            "portal_name": job.portal_name,
        },
    )
    return {"job": _ingestion_payload(job)}


@api_router.post("/ingestion/{job_id}/discard")
async def discard_ingestion_job(
    job_id: UUID,
    payload: IngestionDiscardPayload,
    repository: TenderIngestionJobRepository = Depends(get_ingestion_job_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Terminally skip a failed/quarantined ingestion job without retrying."""

    try:
        job = await repository.discard(job_id, payload.reason)
    except IngestionRepositoryError as exc:
        message = str(exc)
        code = status.HTTP_404_NOT_FOUND if "does not exist" in message else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=message) from exc

    await audit.append(
        event_type=AuditEventType.SYSTEM_INGESTION_DISCARDED,
        actor=principal.sub,
        entity_type="tender_ingestion_job",
        entity_id=str(job.id),
        payload={
            "tender_id": str(job.tender_id),
            "portal_name": job.portal_name,
            "reason": payload.reason,
        },
    )
    return {"job": _ingestion_payload(job)}


@api_router.post("/ingestion/release-stale")
async def release_stale_ingestion_locks(
    payload: ReleaseStaleLocksPayload,
    repository: TenderIngestionJobRepository = Depends(get_ingestion_job_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Reset ingestion jobs whose processing lock exceeded the threshold."""

    released = await repository.release_stale(
        older_than_seconds=payload.older_than_seconds,
    )
    await audit.append(
        event_type=AuditEventType.SYSTEM_INGESTION_LOCKS_RELEASED,
        actor=principal.sub,
        entity_type="tender_ingestion_job",
        entity_id="stale-locks",
        payload={
            "released": released,
            "older_than_seconds": payload.older_than_seconds,
        },
    )
    return {
        "released": released,
        "older_than_seconds": payload.older_than_seconds,
    }


def _trigger_error(exc: WorkerCommandError) -> HTTPException:
    message = str(exc)
    if "unknown portal" in message:
        code = status.HTTP_404_NOT_FOUND
    elif "is disabled" in message:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(status_code=code, detail=message)


@api_router.get("/worker-commands")
async def list_worker_commands(
    status_filter: WorkerCommandStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: WorkerCommandRepository = Depends(get_worker_command_repository),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Durable admin-triggered worker command history."""

    rows = await repository.list_commands(
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_commands(status_filter=status_filter)
    return {
        "commands": [row.as_dict() for row in rows],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@api_router.post("/worker-commands/{command_id}/cancel")
async def cancel_worker_command(
    command_id: UUID,
    payload: WorkerCommandCancelPayload,
    repository: WorkerCommandRepository = Depends(get_worker_command_repository),
    launcher: WorkerCommandLauncher = Depends(get_worker_command_launcher),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Cancel a queued/running worker command and signal its process."""

    try:
        command = await repository.cancel(command_id, payload.reason)
    except WorkerCommandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkerCommandConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    terminated = False
    if command.pid is not None:
        try:
            terminated = launcher.terminate(command.pid)
        except WorkerCommandError:
            logger.exception("could not terminate worker command id=%s pid=%s", command.id, command.pid)
    await audit.append(
        event_type=AuditEventType.SYSTEM_WORKER_COMMAND_CANCELLED,
        actor=principal.sub,
        entity_type="worker_command",
        entity_id=str(command.id),
        payload={
            "reason": payload.reason,
            "command_type": command.command_type,
            "pid": command.pid,
            "terminated": terminated,
        },
    )
    return {"command": command.as_dict(), "terminated": terminated}


@api_router.post("/worker-runs/scrape")
async def trigger_scrape_run(
    payload: WorkerScrapeTriggerPayload,
    repository: WorkerCommandRepository = Depends(get_worker_command_repository),
    launcher: WorkerCommandLauncher = Depends(get_worker_command_launcher),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Launch a durable, detached scrape worker for one portal or all targets."""

    try:
        command = await repository.create(
            command_type="scrape",
            requested_by=principal.sub,
            portal=payload.portal,
            max_pages=payload.max_pages,
        )
    except WorkerCommandConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    try:
        result = launcher.launch_scrape(
            portal=payload.portal,
            max_pages=payload.max_pages,
            command_id=str(command.id),
        )
    except WorkerCommandError as exc:
        await repository.finish(command.id, status="failed", error=str(exc))
        raise _trigger_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_WORKER_SCRAPE_TRIGGERED,
        actor=principal.sub,
        entity_type="worker_command",
        entity_id=str(command.id),
        payload={
            "portal": payload.portal,
            "max_pages": payload.max_pages,
            "pid": result.pid,
        },
    )
    return {**result.as_dict(), "worker_command": command.as_dict()}


@api_router.post("/worker-runs/ingestion")
async def trigger_ingestion_run(
    payload: WorkerIngestionTriggerPayload,
    repository: WorkerCommandRepository = Depends(get_worker_command_repository),
    launcher: WorkerCommandLauncher = Depends(get_worker_command_launcher),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Launch a durable, detached ingestion worker for a bounded batch."""

    try:
        command = await repository.create(
            command_type="ingestion",
            requested_by=principal.sub,
            batch_limit=payload.limit,
        )
    except WorkerCommandConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    try:
        result = launcher.launch_ingestion(limit=payload.limit, command_id=str(command.id))
    except WorkerCommandError as exc:
        await repository.finish(command.id, status="failed", error=str(exc))
        raise _trigger_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SYSTEM_INGESTION_TRIGGERED,
        actor=principal.sub,
        entity_type="worker_command",
        entity_id=str(command.id),
        payload={"limit": payload.limit, "pid": result.pid},
    )
    return {**result.as_dict(), "worker_command": command.as_dict()}


@api_router.get("/portals")
async def list_portals(
    settings: Settings = Depends(get_app_settings),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Configured portal targets available to the read-only probe dialog."""

    try:
        targets = load_portal_targets(settings.portal_config_file, enabled_only=False)
    except PortalConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {
        "portals": [
            {
                "id": target.id,
                "name": target.name,
                "engine": target.engine,
                "enabled": target.enabled,
                "api_feed": target.api is not None,
            }
            for target in targets
        ],
        "count": len(targets),
    }


@api_router.post("/portals/{portal_id}/probe")
async def probe_portal_target(
    portal_id: str,
    max_pages: int = Query(default=1, ge=1, le=5),
    settings: Settings = Depends(get_app_settings),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    """Run the read-only P7.3 probe for one configured portal."""

    try:
        targets = load_portal_targets(settings.portal_config_file, enabled_only=False)
    except PortalConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    target = next((item for item in targets if item.id == portal_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="portal not found")

    try:
        report = await probe_portal(target, settings, max_pages=max_pages)
    except Exception as exc:
        logger.exception("portal probe failed portal=%s", portal_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"portal probe failed: {exc}",
        ) from exc

    payload = report.as_dict()
    await audit.append(
        event_type=AuditEventType.SYSTEM_PORTAL_PROBED,
        actor=principal.sub,
        entity_type="portal",
        entity_id=portal_id,
        payload={
            "notices_discovered": report.notices_discovered,
            "notices_parsed": report.notices_parsed,
            "zero_results": report.zero_results,
            "errors": len(report.errors),
            "warnings": len(report.warnings),
        },
    )
    return payload


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
