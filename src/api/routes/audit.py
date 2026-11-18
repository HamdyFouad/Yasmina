"""Admin-only GoBD audit log viewer and streaming exports."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from src.api.deps import (
    get_audit_log_repository,
    get_audit_service,
    require_roles,
)
from src.api.security import Principal, Role
from src.models.enums import AuditEventType
from src.repositories.audit_repository import AuditLogRepository
from src.services.audit_export import build_xlsx, csv_stream, export_filename, stream_file
from src.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


def _filters(
    *,
    event_type: AuditEventType | None,
    actor: str | None,
    entity_type: str | None,
    entity_id: str | None,
    search: str | None,
    date_from: date | None,
    date_to: date | None,
) -> dict[str, object]:
    return {
        "event_type": event_type.value if event_type is not None else None,
        "actor": actor,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "search": search,
        "date_from": date_from,
        "date_to": date_to,
    }


@router.get("/event-types")
async def audit_event_types(
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    return {"event_types": [event.value for event in AuditEventType]}


@router.get("/logs")
async def list_audit_logs(
    event_type: AuditEventType | None = Query(default=None),
    actor: str | None = Query(default=None, max_length=128),
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=128),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: AuditLogRepository = Depends(get_audit_log_repository),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    filters = _filters(
        event_type=event_type,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )
    rows = await repository.list_logs(limit=limit, offset=offset, **filters)
    total = await repository.count_logs(**filters)
    return {
        "logs": [row.as_dict() for row in rows],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/logs/export")
async def export_audit_logs(
    format_name: Literal["csv", "xlsx"] = Query(default="csv", alias="format"),
    event_type: AuditEventType | None = Query(default=None),
    actor: str | None = Query(default=None, max_length=128),
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=128),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    repository: AuditLogRepository = Depends(get_audit_log_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> StreamingResponse:
    filters = _filters(
        event_type=event_type,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )
    await audit.append(
        event_type=AuditEventType.SYSTEM_AUDIT_EXPORTED,
        actor=principal.sub,
        entity_type="audit_log",
        entity_id="export",
        payload={
            "format": format_name,
            "filters": {
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in filters.items()
                if value not in (None, "")
            },
        },
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{export_filename(format_name)}"',
        "Cache-Control": "no-store",
    }
    if format_name == "csv":
        return StreamingResponse(
            csv_stream(repository, filters=dict(filters)),
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )
    target = await build_xlsx(repository, filters=dict(filters))
    return StreamingResponse(
        stream_file(target),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/health")
async def audit_health(
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, object]:
    return {"status": "ok", "immutable": True, "source": "audit_logs"}


__all__ = ["router"]
