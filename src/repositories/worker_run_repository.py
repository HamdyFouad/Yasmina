"""Persistence for worker run history and operational metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import WorkerRun

__all__ = ["WorkerRunRepository", "WorkerRunView"]


class WorkerRunRepositoryError(ValueError):
    """Raised when a worker run cannot be created or updated."""


@dataclass(frozen=True, slots=True)
class WorkerRunView:
    id: UUID
    run_type: str
    resource: str
    status: str
    worker_id: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    items_processed: int
    errors_count: int
    error: str | None
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "run_type": self.run_type,
            "resource": self.resource,
            "status": self.status,
            "worker_id": self.worker_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at is not None else None,
            "duration_ms": self.duration_ms,
            "items_processed": self.items_processed,
            "errors_count": self.errors_count,
            "error": self.error,
            "details": self.details,
        }


def _view(row: WorkerRun) -> WorkerRunView:
    return WorkerRunView(
        id=row.id,
        run_type=row.run_type,
        resource=row.resource,
        status=row.status,
        worker_id=row.worker_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        items_processed=row.items_processed,
        errors_count=row.errors_count,
        error=row.error,
        details=dict(row.details or {}),
    )


class WorkerRunRepository:
    """Create and finish rows in ``worker_runs``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        *,
        run_type: str,
        resource: str,
        worker_id: str,
        details: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        commit: bool = True,
    ) -> WorkerRunView:
        if run_type not in {"scrape", "ingestion"}:
            raise WorkerRunRepositoryError(f"unsupported worker run type {run_type!r}")
        row = WorkerRun(
            run_type=run_type,
            resource=resource,
            status="running",
            worker_id=worker_id,
            started_at=started_at or datetime.now(UTC),
            details=dict(details or {}),
        )
        self.session.add(row)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return _view(row)

    async def finish(
        self,
        run_id: UUID,
        *,
        status: str,
        items_processed: int = 0,
        errors_count: int = 0,
        error: str | None = None,
        details: dict[str, Any] | None = None,
        finished_at: datetime | None = None,
        commit: bool = True,
    ) -> WorkerRunView:
        if status not in {"succeeded", "failed", "skipped", "cancelled"}:
            raise WorkerRunRepositoryError(f"unsupported terminal run status {status!r}")
        row = await self.session.get(WorkerRun, run_id)
        if row is None:
            raise WorkerRunRepositoryError(f"worker run {run_id} does not exist")
        current = finished_at or datetime.now(UTC)
        started = row.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        row.status = status
        row.finished_at = current
        row.duration_ms = max(0, int((current - started).total_seconds() * 1000))
        row.items_processed = max(0, items_processed)
        row.errors_count = max(0, errors_count)
        row.error = error[:8000] if error else None
        if details is not None:
            row.details = dict(details)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return _view(row)

    async def recent(self, *, limit: int = 20) -> list[WorkerRunView]:
        rows = (
            (
                await self.session.execute(
                    select(WorkerRun).order_by(desc(WorkerRun.started_at)).limit(max(1, limit))
                )
            )
            .scalars()
            .all()
        )
        return [_view(row) for row in rows]

    async def latest_by_type(self) -> dict[str, WorkerRunView]:
        latest: dict[str, WorkerRunView] = {}
        for run_type in ("scrape", "ingestion"):
            row = (
                (
                    await self.session.execute(
                        select(WorkerRun)
                        .where(WorkerRun.run_type == run_type)
                        .order_by(desc(WorkerRun.started_at))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if row is not None:
                latest[run_type] = _view(row)
        return latest

    async def status_counts(self) -> dict[str, int]:
        rows = (
            await self.session.execute(select(WorkerRun.status, func.count()).group_by(WorkerRun.status))
        ).all()
        counts = {"running": 0, "succeeded": 0, "failed": 0, "skipped": 0, "cancelled": 0}
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts
