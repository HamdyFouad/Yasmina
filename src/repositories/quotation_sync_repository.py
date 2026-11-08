"""Durable outbox for asynchronous ERPNext quotation synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import QuotationSyncJob

__all__ = [
    "QuotationSyncJobRepository",
    "QuotationSyncJobView",
    "QuotationSyncRepositoryError",
]


class QuotationSyncRepositoryError(ValueError):
    """Raised when a quotation sync job cannot be queued or claimed."""


@dataclass(frozen=True, slots=True)
class QuotationSyncJobView:
    id: UUID
    offer_id: UUID
    offer_number: str
    status: str
    attempts: int
    max_attempts: int
    available_at: datetime
    quotation_name: str | None
    last_error: str | None


def _view(row: QuotationSyncJob) -> QuotationSyncJobView:
    return QuotationSyncJobView(
        id=row.id,
        offer_id=row.offer_id,
        offer_number=row.offer_number,
        status=row.status,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        available_at=row.available_at,
        quotation_name=row.quotation_name,
        last_error=row.last_error,
    )


class QuotationSyncJobRepository:
    """Idempotent enqueueing and lease-based claiming."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_offer(self, offer_id: UUID) -> QuotationSyncJobView | None:
        row = (
            (
                await self.session.execute(
                    select(QuotationSyncJob).where(QuotationSyncJob.offer_id == offer_id)
                )
            )
            .scalars()
            .first()
        )
        return _view(row) if row is not None else None

    async def enqueue(
        self,
        offer_id: UUID,
        *,
        offer_number: str,
        max_attempts: int = 6,
        commit: bool = True,
    ) -> tuple[QuotationSyncJobView, bool]:
        """Create one job per offer; replay returns the existing row."""

        existing = await self.get_by_offer(offer_id)
        if existing is not None:
            return existing, False
        row = QuotationSyncJob(
            offer_id=offer_id,
            offer_number=offer_number,
            status="pending",
            attempts=0,
            max_attempts=max_attempts,
        )
        self.session.add(row)
        try:
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            existing = await self.get_by_offer(offer_id)
            if existing is not None:
                return existing, False
            raise QuotationSyncRepositoryError(
                f"quotation sync job for offer {offer_id} already exists"
            ) from exc
        return _view(row), True

    async def claim_pending(
        self,
        *,
        limit: int = 10,
        worker_id: str,
        now: datetime | None = None,
        stale_after_seconds: int = 900,
    ) -> list[QuotationSyncJobView]:
        current = now or datetime.now(UTC)
        stale_before = current - timedelta(seconds=stale_after_seconds)
        statement = (
            select(QuotationSyncJob)
            .where(
                or_(
                    (QuotationSyncJob.status == "pending") & (QuotationSyncJob.available_at <= current),
                    (QuotationSyncJob.status == "processing") & (QuotationSyncJob.locked_at <= stale_before),
                )
            )
            .order_by(QuotationSyncJob.available_at, QuotationSyncJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        for row in rows:
            row.status = "processing"
            row.attempts += 1
            row.locked_at = current
            row.locked_by = worker_id
        if rows:
            await self.session.commit()
        return [_view(row) for row in rows]

    async def complete(self, job_id: UUID, *, quotation_name: str) -> None:
        row = await self.session.get(QuotationSyncJob, job_id)
        if row is None:
            raise QuotationSyncRepositoryError(f"quotation sync job {job_id} does not exist")
        row.status = "succeeded"
        row.quotation_name = quotation_name[:140]
        row.locked_at = None
        row.locked_by = None
        row.last_error = None
        await self.session.commit()

    async def skip(self, job_id: UUID, reason: str | None = None) -> None:
        row = await self.session.get(QuotationSyncJob, job_id)
        if row is None:
            raise QuotationSyncRepositoryError(f"quotation sync job {job_id} does not exist")
        row.status = "skipped"
        row.locked_at = None
        row.locked_by = None
        row.last_error = reason[:8000] if reason else None
        await self.session.commit()

    async def fail(
        self,
        job_id: UUID,
        error: str,
        *,
        retry_backoff_seconds: int,
        now: datetime | None = None,
    ) -> str:
        row = await self.session.get(QuotationSyncJob, job_id)
        if row is None:
            raise QuotationSyncRepositoryError(f"quotation sync job {job_id} does not exist")
        current = now or datetime.now(UTC)
        row.last_error = error[:8000]
        row.locked_at = None
        row.locked_by = None
        if row.attempts >= row.max_attempts:
            row.status = "failed"
        else:
            row.status = "pending"
            row.available_at = current + timedelta(seconds=max(1, retry_backoff_seconds))
        await self.session.commit()
        return row.status

    async def release(self, job_id: UUID, *, now: datetime | None = None) -> None:
        row = await self.session.get(QuotationSyncJob, job_id)
        if row is None or row.status != "processing":
            return
        row.status = "pending"
        row.attempts = max(0, row.attempts - 1)
        row.available_at = now or datetime.now(UTC)
        row.locked_at = None
        row.locked_by = None
        await self.session.commit()

    async def status_counts(self) -> dict[str, int]:
        rows = (
            await self.session.execute(
                select(QuotationSyncJob.status, func.count()).group_by(QuotationSyncJob.status)
            )
        ).all()
        counts = {"pending": 0, "processing": 0, "succeeded": 0, "failed": 0, "skipped": 0}
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts
