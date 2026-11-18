"""Durable lifecycle for admin-triggered worker commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import WorkerCommand

__all__ = [
    "WorkerCommandConflict",
    "WorkerCommandNotFound",
    "WorkerCommandRepository",
    "WorkerCommandView",
]


class WorkerCommandConflict(ValueError):
    """Raised when an active command already exists or a state transition is invalid."""


class WorkerCommandNotFound(ValueError):
    """Raised when a command does not exist."""


@dataclass(frozen=True, slots=True)
class WorkerCommandView:
    id: UUID
    command_type: str
    status: str
    portal: str | None
    max_pages: int | None
    batch_limit: int | None
    pid: int | None
    requested_by: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    details: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "command_type": self.command_type,
            "status": self.status,
            "portal": self.portal,
            "max_pages": self.max_pages,
            "batch_limit": self.batch_limit,
            "pid": self.pid,
            "requested_by": self.requested_by,
            "started_at": self.started_at.isoformat() if self.started_at is not None else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at is not None else None,
            "error": self.error,
            "details": dict(self.details or {}),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _view(row: WorkerCommand) -> WorkerCommandView:
    return WorkerCommandView(
        id=row.id,
        command_type=row.command_type,
        status=row.status,
        portal=row.portal,
        max_pages=row.max_pages,
        batch_limit=row.batch_limit,
        pid=row.pid,
        requested_by=row.requested_by,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        details=dict(row.details or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class WorkerCommandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        command_type: str,
        requested_by: str,
        portal: str | None = None,
        max_pages: int | None = None,
        batch_limit: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> WorkerCommandView:
        if command_type not in {"scrape", "ingestion"}:
            raise ValueError(f"unsupported worker command type {command_type!r}")
        row = WorkerCommand(
            command_type=command_type,
            status="queued",
            portal=portal,
            max_pages=max_pages,
            batch_limit=batch_limit,
            requested_by=requested_by,
            details=dict(details or {}),
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise WorkerCommandConflict(
                f"another {command_type} worker command is already queued or running"
            ) from exc
        return _view(row)

    async def get(self, command_id: UUID) -> WorkerCommandView | None:
        row = await self.session.get(WorkerCommand, command_id)
        return None if row is None else _view(row)

    async def list_commands(
        self,
        *,
        status_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkerCommandView]:
        statement = (
            select(WorkerCommand).order_by(WorkerCommand.created_at.desc()).offset(offset).limit(limit)
        )
        if status_filter is not None:
            statement = statement.where(WorkerCommand.status == status_filter)
        rows = (await self.session.execute(statement)).scalars().all()
        return [_view(row) for row in rows]

    async def count_commands(self, *, status_filter: str | None = None) -> int:
        statement = select(func.count()).select_from(WorkerCommand)
        if status_filter is not None:
            statement = statement.where(WorkerCommand.status == status_filter)
        return int((await self.session.execute(statement)).scalar_one())

    async def active_for_type(self, command_type: str) -> WorkerCommandView | None:
        row = (
            (
                await self.session.execute(
                    select(WorkerCommand).where(
                        WorkerCommand.command_type == command_type,
                        WorkerCommand.status.in_(("queued", "running")),
                    )
                )
            )
            .scalars()
            .first()
        )
        return None if row is None else _view(row)

    async def mark_running(self, command_id: UUID, pid: int) -> bool:
        row = await self.session.get(WorkerCommand, command_id)
        if row is None:
            raise WorkerCommandNotFound("worker command not found")
        if row.status != "queued":
            return False
        row.status = "running"
        row.pid = pid
        row.started_at = datetime.now(UTC)
        await self.session.commit()
        return True

    async def finish(
        self,
        command_id: UUID,
        *,
        status: str,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> bool:
        if status not in {"succeeded", "failed"}:
            raise ValueError(f"unsupported terminal worker command status {status!r}")
        row = await self.session.get(WorkerCommand, command_id)
        if row is None:
            raise WorkerCommandNotFound("worker command not found")
        if row.status not in {"queued", "running"}:
            return False
        row.status = status
        row.finished_at = datetime.now(UTC)
        row.error = error[:8000] if error else None
        if details is not None:
            row.details = dict(details)
        await self.session.commit()
        return True

    async def cancel(self, command_id: UUID, reason: str) -> WorkerCommandView:
        row = await self.session.get(WorkerCommand, command_id)
        if row is None:
            raise WorkerCommandNotFound("worker command not found")
        if row.status not in {"queued", "running"}:
            raise WorkerCommandConflict("only queued or running commands can be cancelled")
        row.status = "cancelled"
        row.finished_at = datetime.now(UTC)
        row.error = reason[:8000]
        await self.session.commit()
        return _view(row)
