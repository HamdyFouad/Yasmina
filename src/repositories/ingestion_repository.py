"""Durable outbox and watermark repositories for portal ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import ScraperState, TenderIngestionJob
from src.models.orm import Tender as TenderORM

__all__ = [
    "IngestionJobView",
    "ScraperStateRepository",
    "TenderIngestionJobRepository",
]


class IngestionRepositoryError(ValueError):
    """Raised when a job cannot be queued/claimed."""


@dataclass(frozen=True, slots=True)
class IngestionJobView:
    id: UUID
    tender_id: UUID
    portal_name: str
    status: str
    attempts: int
    max_attempts: int
    attachments: tuple[dict[str, Any], ...]
    raw_object: str | None
    available_at: datetime | None = None
    locked_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tender_number: str | None = None
    tender_title: str | None = None
    quarantined: bool = False
    quarantine_reason: str | None = None


def _loaded(row: TenderIngestionJob, name: str) -> Any:
    """Read a column only when SQLAlchemy already loaded it.

    Mutating helpers commit before building their response view. Commit with
    ``expire_on_commit=False`` keeps most columns loaded, but ``onupdate`` /
    server-default columns can still be expired. Touching those from a sync view
    builder inside async code raises ``MissingGreenlet``; a missing value is
    preferable to an implicit database round-trip.
    """

    return row.__dict__.get(name)


def _job_view(
    row: TenderIngestionJob,
    *,
    tender_number: str | None = None,
    tender_title: str | None = None,
) -> IngestionJobView:
    return IngestionJobView(
        id=row.id,
        tender_id=row.tender_id,
        portal_name=row.portal_name,
        status=row.status,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        attachments=tuple(dict(item) for item in (row.attachments or [])),
        raw_object=row.raw_object,
        available_at=_loaded(row, "available_at"),
        locked_at=_loaded(row, "locked_at"),
        last_error=_loaded(row, "last_error"),
        created_at=_loaded(row, "created_at"),
        updated_at=_loaded(row, "updated_at"),
        tender_number=tender_number,
        tender_title=tender_title,
        quarantined=bool(_loaded(row, "quarantined")),
        quarantine_reason=_loaded(row, "quarantine_reason"),
    )


class TenderIngestionJobRepository:
    """Transactional outbox for scraper metadata and attachments."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        tender_id: UUID,
        *,
        portal_name: str,
        attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        raw_object: str | None = None,
        commit: bool = True,
    ) -> tuple[IngestionJobView, bool]:
        existing = (
            (
                await self.session.execute(
                    select(TenderIngestionJob).where(TenderIngestionJob.tender_id == tender_id)
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return _job_view(existing), False

        job = TenderIngestionJob(
            tender_id=tender_id,
            portal_name=portal_name,
            status="pending",
            attempts=0,
            attachments=[dict(item) for item in attachments],
            raw_object=raw_object,
        )
        self.session.add(job)
        try:
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise IngestionRepositoryError(f"ingestion job for tender {tender_id} already exists") from exc
        return _job_view(job), True

    async def claim_pending(
        self,
        *,
        limit: int = 10,
        worker_id: str,
        now: datetime | None = None,
        stale_after_seconds: int = 900,
    ) -> list[IngestionJobView]:
        current = now or datetime.now(UTC)
        stale_before = current - timedelta(seconds=stale_after_seconds)
        statement = (
            select(TenderIngestionJob)
            .where(
                or_(
                    ((TenderIngestionJob.status == "pending") & (TenderIngestionJob.available_at <= current)),
                    (
                        (TenderIngestionJob.status == "processing")
                        & (TenderIngestionJob.locked_at <= stale_before)
                    ),
                )
            )
            .order_by(TenderIngestionJob.available_at, TenderIngestionJob.created_at)
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
        return [_job_view(row) for row in rows]

    async def complete(self, job_id: UUID) -> None:
        row = await self.session.get(TenderIngestionJob, job_id)
        if row is None:
            raise IngestionRepositoryError(f"ingestion job {job_id} does not exist")
        row.status = "completed"
        row.locked_at = None
        row.locked_by = None
        row.last_error = None
        await self.session.commit()

    async def skip(self, job_id: UUID, reason: str | None = None) -> None:
        row = await self.session.get(TenderIngestionJob, job_id)
        if row is None:
            raise IngestionRepositoryError(f"ingestion job {job_id} does not exist")
        row.status = "skipped"
        row.locked_at = None
        row.locked_by = None
        row.last_error = reason
        await self.session.commit()

    async def fail(
        self,
        job_id: UUID,
        error: str,
        *,
        retry_backoff_seconds: int = 60,
        now: datetime | None = None,
    ) -> str:
        """Record a failed attempt and return the resulting status.

        ``pending`` means another attempt was scheduled; ``failed`` is the
        terminal dead-letter state reached at ``max_attempts``.
        """

        row = await self.session.get(TenderIngestionJob, job_id)
        if row is None:
            raise IngestionRepositoryError(f"ingestion job {job_id} does not exist")
        current = now or datetime.now(UTC)
        row.last_error = error[:8000]
        row.locked_at = None
        row.locked_by = None
        if row.attempts >= row.max_attempts:
            row.status = "failed"
        else:
            row.status = "pending"
            row.available_at = current + timedelta(seconds=retry_backoff_seconds)
        await self.session.commit()
        return row.status

    async def security_fail(self, job_id: UUID, error: str) -> None:
        """Move a malware-positive job directly to the terminal failed state.

        Security rejections must never be retried: re-downloading the same
        infected object would only repeat the scan. The error is prefixed so
        operators and the worker health endpoint can distinguish a security
        quarantine from an ordinary parsing failure.
        """

        row = await self.session.get(TenderIngestionJob, job_id)
        if row is None:
            raise IngestionRepositoryError(f"ingestion job {job_id} does not exist")
        row.status = "failed"
        row.locked_at = None
        row.locked_by = None
        row.last_error = f"SECURITY: {error}"[:8000]
        row.quarantined = True
        row.quarantine_reason = error[:8000]
        await self.session.commit()

    async def release(self, job_id: UUID, *, now: datetime | None = None) -> None:
        """Return a claimed-but-unstarted job to the queue (graceful shutdown)."""

        row = await self.session.get(TenderIngestionJob, job_id)
        if row is None:
            raise IngestionRepositoryError(f"ingestion job {job_id} does not exist")
        if row.status != "processing":
            return
        row.status = "pending"
        row.attempts = max(0, row.attempts - 1)
        row.available_at = now or datetime.now(UTC)
        row.locked_at = None
        row.locked_by = None
        await self.session.commit()

    async def pending_count(self) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(TenderIngestionJob)
                    .where(TenderIngestionJob.status == "pending")
                )
            ).scalar_one()
        )

    async def status_counts(self) -> dict[str, int]:
        rows = (
            await self.session.execute(
                select(TenderIngestionJob.status, func.count()).group_by(TenderIngestionJob.status)
            )
        ).all()
        counts = {"pending": 0, "processing": 0, "completed": 0, "skipped": 0, "failed": 0}
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts

    async def list_jobs(
        self,
        *,
        status_filter: str | None = None,
        quarantined: bool | None = None,
        queue: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[IngestionJobView]:
        """List ingestion jobs with the linked tender for operations consoles."""

        statement = (
            select(TenderIngestionJob, TenderORM.tender_number, TenderORM.title)
            .join(TenderORM, TenderORM.id == TenderIngestionJob.tender_id)
            .order_by(TenderIngestionJob.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if status_filter is not None:
            statement = statement.where(TenderIngestionJob.status == status_filter)
        if quarantined is True:
            statement = statement.where(TenderIngestionJob.quarantined.is_(True))
        elif quarantined is False:
            statement = statement.where(TenderIngestionJob.quarantined.is_(False))
        if queue is True:
            statement = statement.where(TenderIngestionJob.status.in_(("pending", "processing")))
        elif queue is False:
            statement = statement.where(TenderIngestionJob.status.not_in(("pending", "processing")))
        rows = (await self.session.execute(statement)).all()
        return [
            _job_view(job, tender_number=tender_number, tender_title=title)
            for job, tender_number, title in rows
        ]

    async def count_jobs(
        self,
        *,
        status_filter: str | None = None,
        quarantined: bool | None = None,
        queue: bool | None = None,
    ) -> int:
        statement = select(func.count()).select_from(TenderIngestionJob)
        if status_filter is not None:
            statement = statement.where(TenderIngestionJob.status == status_filter)
        if quarantined is True:
            statement = statement.where(TenderIngestionJob.quarantined.is_(True))
        elif quarantined is False:
            statement = statement.where(TenderIngestionJob.quarantined.is_(False))
        if queue is True:
            statement = statement.where(TenderIngestionJob.status.in_(("pending", "processing")))
        elif queue is False:
            statement = statement.where(TenderIngestionJob.status.not_in(("pending", "processing")))
        return int((await self.session.execute(statement)).scalar_one())

    async def quarantine_count(self) -> int:
        return await self.count_jobs(status_filter="failed", quarantined=True)

    async def retry_failed(self, job_id: UUID) -> IngestionJobView:
        """Requeue a terminal failed job for a fresh attempt."""

        row = await self.session.get(TenderIngestionJob, job_id)
        if row is None:
            raise IngestionRepositoryError(f"ingestion job {job_id} does not exist")
        if row.status != "failed":
            raise IngestionRepositoryError("only failed ingestion jobs can be retried")
        row.status = "pending"
        row.attempts = 0
        row.available_at = datetime.now(UTC)
        row.locked_at = None
        row.locked_by = None
        row.last_error = None
        row.quarantined = False
        row.quarantine_reason = None
        await self.session.commit()
        return _job_view(row)

    async def discard(self, job_id: UUID, reason: str) -> IngestionJobView:
        """Terminally skip a failed job without retrying it."""

        row = await self.session.get(TenderIngestionJob, job_id)
        if row is None:
            raise IngestionRepositoryError(f"ingestion job {job_id} does not exist")
        if row.status != "failed":
            raise IngestionRepositoryError("only failed ingestion jobs can be discarded")
        row.status = "skipped"
        row.locked_at = None
        row.locked_by = None
        row.last_error = reason[:8000]
        # Keep the quarantine reason as history, but the job is no longer an
        # active quarantine once an operator has closed it out.
        row.quarantined = False
        await self.session.commit()
        return _job_view(row)

    async def release_stale(
        self,
        *,
        older_than_seconds: int = 900,
        now: datetime | None = None,
    ) -> int:
        """Reset processing jobs whose worker lock has gone stale."""

        if older_than_seconds < 1:
            raise IngestionRepositoryError("older_than_seconds must be positive")
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(seconds=older_than_seconds)
        rows = (
            (
                await self.session.execute(
                    select(TenderIngestionJob)
                    .where(
                        TenderIngestionJob.status == "processing",
                        or_(
                            TenderIngestionJob.locked_at.is_(None),
                            TenderIngestionJob.locked_at < cutoff,
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = "pending"
            row.attempts = max(0, row.attempts - 1)
            row.available_at = current
            row.locked_at = None
            row.locked_by = None
        if rows:
            await self.session.commit()
        return len(rows)


class ScraperStateRepository:
    """Read and advance the per-portal scraping watermark."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, portal_name: str) -> ScraperState | None:
        return await self.session.get(ScraperState, portal_name)

    async def mark_started(
        self,
        portal_name: str,
        *,
        at: datetime | None = None,
        commit: bool = True,
    ) -> ScraperState:
        state = await self.get(portal_name)
        if state is None:
            state = ScraperState(portal_name=portal_name)
            self.session.add(state)
        state.last_scraped_at = at or datetime.now(UTC)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return state

    async def mark_success(
        self,
        portal_name: str,
        *,
        at: datetime | None = None,
        commit: bool = True,
    ) -> ScraperState:
        state = await self.get(portal_name)
        current = at or datetime.now(UTC)
        if state is None:
            state = ScraperState(portal_name=portal_name)
            self.session.add(state)
        state.last_scraped_at = current
        state.last_success_at = current
        state.last_error = None
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return state

    async def mark_failure(
        self,
        portal_name: str,
        error: str,
        *,
        commit: bool = True,
    ) -> None:
        state = await self.get(portal_name)
        if state is None:
            state = ScraperState(portal_name=portal_name, last_error=error[:8000])
            self.session.add(state)
        else:
            state.last_error = error[:8000]
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
