"""Persistent idempotency ledger for external integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import IntegrationIdempotency

__all__ = ["IdempotencyRecord", "IntegrationRepository"]


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    operation: str
    request_hash: str
    status: str
    response: dict[str, Any] | None


def _record(row: IntegrationIdempotency) -> IdempotencyRecord:
    return IdempotencyRecord(
        key=row.key,
        operation=row.operation,
        request_hash=row.request_hash,
        status=row.status,
        response=dict(row.response) if row.response is not None else None,
    )


class IntegrationRepository:
    """Atomically reserve and complete idempotency keys."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> IdempotencyRecord | None:
        row = (
            await self.session.execute(
                select(IntegrationIdempotency).where(IntegrationIdempotency.key == key)
            )
        ).scalar_one_or_none()
        return _record(row) if row is not None else None

    async def reserve(
        self,
        *,
        key: str,
        operation: str,
        request_hash: str,
    ) -> tuple[IdempotencyRecord, bool]:
        """Insert a pending key or return the existing record.

        ``INSERT ... ON CONFLICT DO NOTHING`` makes the reservation safe across
        API workers; the boolean tells the caller whether it owns the key.
        """

        statement = (
            pg_insert(IntegrationIdempotency)
            .values(
                key=key,
                operation=operation,
                request_hash=request_hash,
                status="pending",
                response=None,
            )
            .on_conflict_do_nothing(index_elements=[IntegrationIdempotency.key])
            .returning(IntegrationIdempotency)
        )
        inserted = (await self.session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return _record(inserted), True

        existing = (
            await self.session.execute(
                select(IntegrationIdempotency).where(IntegrationIdempotency.key == key)
            )
        ).scalar_one()
        return _record(existing), False

    async def mark_succeeded(self, key: str, response: dict[str, Any]) -> None:
        await self.session.execute(
            update(IntegrationIdempotency)
            .where(IntegrationIdempotency.key == key)
            .values(status="succeeded", response=response, updated_at=func.now())
        )

    async def mark_failed(self, key: str, error: str) -> None:
        await self.session.execute(
            update(IntegrationIdempotency)
            .where(IntegrationIdempotency.key == key)
            .values(status="failed", response={"error": error}, updated_at=func.now())
        )

    async def reopen(self, key: str) -> None:
        """Allow a new attempt after a recorded failure."""

        await self.session.execute(
            update(IntegrationIdempotency)
            .where(IntegrationIdempotency.key == key)
            .values(status="pending", response=None, updated_at=func.now())
        )
