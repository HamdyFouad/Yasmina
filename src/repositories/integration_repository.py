"""Persistent idempotency ledger for external integrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import IntegrationIdempotency

__all__ = ["IdempotencyRecord", "IntegrationRepository", "STALE_PENDING_ERROR"]

STALE_PENDING_ERROR = "stale pending reservation reclaimed after TTL"


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
        stale_after_seconds: int | None = None,
        now: datetime | None = None,
    ) -> tuple[IdempotencyRecord, bool]:
        """Insert a pending key or return the existing record.

        ``INSERT ... ON CONFLICT DO NOTHING`` makes the reservation safe across
        API workers; the boolean tells the caller whether it owns the key. When
        ``stale_after_seconds`` is supplied, a matching pending reservation that
        has not been updated within the TTL is reclaimed for this request. This
        closes the crash-after-reservation loophole without ever reclaiming a
        key whose operation or request body differs.
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

        if (
            stale_after_seconds is not None
            and existing.status == "pending"
            and existing.operation == operation
            and existing.request_hash == request_hash
        ):
            current = now or datetime.now(UTC)
            cutoff = current - timedelta(seconds=max(1, stale_after_seconds))
            reclaimed = (
                await self.session.execute(
                    update(IntegrationIdempotency)
                    .where(
                        IntegrationIdempotency.key == key,
                        IntegrationIdempotency.status == "pending",
                        IntegrationIdempotency.updated_at < cutoff,
                    )
                    .values(
                        status="pending",
                        response=None,
                        updated_at=current,
                    )
                    .returning(IntegrationIdempotency)
                )
            ).scalar_one_or_none()
            if reclaimed is not None:
                return _record(reclaimed), True

        return _record(existing), False

    async def reclaim_stale_pending(
        self,
        *,
        max_age_seconds: int,
        operation: str | None = None,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> int:
        """Mark crashed ``pending`` reservations as retryable failures.

        Returns the number of rows reclaimed (or that would be reclaimed for a
        dry run). Reclaimed keys are recorded as failures, so the next request
        with the same key and body reopens and dispatches it normally.
        """

        current = now or datetime.now(UTC)
        cutoff = current - timedelta(seconds=max(1, max_age_seconds))
        conditions = [
            IntegrationIdempotency.status == "pending",
            IntegrationIdempotency.updated_at < cutoff,
        ]
        if operation is not None:
            conditions.append(IntegrationIdempotency.operation == operation)

        if dry_run:
            count = await self.session.scalar(
                select(func.count()).select_from(IntegrationIdempotency).where(*conditions)
            )
            return int(count or 0)

        result = await self.session.execute(
            update(IntegrationIdempotency)
            .where(*conditions)
            .values(
                status="failed",
                response={"error": STALE_PENDING_ERROR},
                updated_at=current,
            )
            .returning(IntegrationIdempotency.key)
        )
        return len(result.scalars().all())

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
