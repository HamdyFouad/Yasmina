"""Paginated, filterable access to the append-only audit chain."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import AuditLog

__all__ = ["AuditLogRepository", "AuditLogView"]


@dataclass(frozen=True, slots=True)
class AuditLogView:
    id: int
    occurred_at: datetime
    event_type: str
    actor: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    prev_hash: str | None
    entry_hash: str
    signature: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "occurred_at": self.occurred_at.isoformat(),
            "event_type": self.event_type,
            "actor": self.actor,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": dict(self.payload),
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "signature": self.signature,
        }


def _view(row: AuditLog) -> AuditLogView:
    return AuditLogView(
        id=row.id,
        occurred_at=row.occurred_at,
        event_type=row.event_type.value if hasattr(row.event_type, "value") else str(row.event_type),
        actor=row.actor,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        payload=dict(row.payload or {}),
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
        signature=row.signature,
    )


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _statement(
        self,
        *,
        event_type: str | None = None,
        actor: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> Select[AuditLog]:
        statement = select(AuditLog)
        if event_type:
            statement = statement.where(AuditLog.event_type == event_type)
        if actor:
            statement = statement.where(AuditLog.actor.ilike(f"%{actor.strip()}%"))
        if entity_type:
            statement = statement.where(AuditLog.entity_type == entity_type)
        if entity_id:
            statement = statement.where(AuditLog.entity_id == entity_id)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    AuditLog.actor.ilike(pattern),
                    AuditLog.entity_type.ilike(pattern),
                    AuditLog.entity_id.ilike(pattern),
                    cast(AuditLog.payload, Text).ilike(pattern),
                )
            )
        if date_from is not None:
            statement = statement.where(
                AuditLog.occurred_at >= datetime.combine(date_from, time.min, tzinfo=UTC)
            )
        if date_to is not None:
            statement = statement.where(
                AuditLog.occurred_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
            )
        return statement

    async def list_logs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        **filters: Any,
    ) -> list[AuditLogView]:
        statement = self._statement(**filters).order_by(AuditLog.id.desc()).offset(offset).limit(limit)
        rows = (await self.session.execute(statement)).scalars().all()
        return [_view(row) for row in rows]

    async def count_logs(self, **filters: Any) -> int:
        filtered = self._statement(**filters).subquery()
        statement = select(func.count()).select_from(filtered)
        return int((await self.session.execute(statement)).scalar_one())

    async def iter_export(
        self,
        *,
        batch_size: int = 1000,
        **filters: Any,
    ) -> AsyncIterator[AuditLogView]:
        last_id = 0
        while True:
            statement = (
                self._statement(**filters)
                .where(AuditLog.id > last_id)
                .order_by(AuditLog.id.asc())
                .limit(batch_size)
            )
            rows = (await self.session.execute(statement)).scalars().all()
            if not rows:
                break
            for row in rows:
                yield _view(row)
            last_id = rows[-1].id
            if len(rows) < batch_size:
                break
