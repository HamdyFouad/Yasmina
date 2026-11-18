"""Live PostgreSQL tests for the ingestion outbox and scraper watermark."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from src.config import Settings
from src.db import dispose_engine, get_engine, get_session_factory
from src.models.enums import ProcurementProcedure, TenderSourceFormat
from src.models.orm import (
    ScraperState,
    TenderIngestionJob,
    WorkerCommand,
    WorkerRun,
)
from src.models.orm import Tender as TenderORM
from src.models.schemas import Tender
from src.repositories.ingestion_repository import (
    ScraperStateRepository,
    TenderIngestionJobRepository,
)
from src.repositories.tender_repository import TenderRepository
from src.repositories.worker_command_repository import (
    WorkerCommandConflict,
    WorkerCommandRepository,
)
from src.repositories.worker_run_repository import WorkerRunRepository
from src.services.worker_lock import DatabasePortalLock

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="live-test-jwt-secret-with-at-least-32-bytes",
        api_admin_password="test-admin-password",
    )


def make_tender() -> Tender:
    suffix = datetime.now(UTC).strftime("%H%M%S%f")
    return Tender(
        tender_number=f"OUTBOX-{suffix}",
        title=f"Rahmenvertrag Hygiene {suffix}",
        client_name=f"Stadt Muster {suffix}",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.PORTAL,
        submission_deadline=NOW,
    )


async def test_outbox_is_claimed_completed_and_watermark_advances() -> None:
    factory = get_session_factory(settings())
    tender = make_tender()
    try:
        async with factory() as session:
            repository = TenderRepository(session)
            jobs = TenderIngestionJobRepository(session)
            states = ScraperStateRepository(session)
            await states.mark_started("vergabe.bayern", at=NOW)
            stored = await repository.save(tender, source_object="scrapers/test", commit=False)
            job, created = await jobs.enqueue(
                stored.tender.id,
                portal_name="vergabe.bayern",
                attachments=[
                    {
                        "object_name": "scrapers/test/attachments/lv.pdf",
                        "filename": "lv.pdf",
                        "content_type": "application/pdf",
                        "kind": "pdf",
                    }
                ],
                raw_object="scrapers/test/listing.html",
                commit=False,
            )
            await session.commit()

            assert created is True
            claimed = await jobs.claim_pending(limit=1, worker_id="live-test")
            assert [item.id for item in claimed] == [job.id]
            assert claimed[0].status == "processing"
            assert claimed[0].attempts == 1
            await jobs.complete(job.id)
            await states.mark_success("vergabe.bayern", at=NOW)

        async with factory() as session:
            state = await session.get(ScraperState, "vergabe.bayern")
            assert state is not None
            assert state.last_success_at is not None
            await session.execute(delete(TenderIngestionJob).where(TenderIngestionJob.id == job.id))
            await session.execute(delete(TenderORM).where(TenderORM.id == tender.id))
            await session.execute(delete(ScraperState).where(ScraperState.portal_name == "vergabe.bayern"))
            await session.commit()
    finally:
        await dispose_engine()


async def test_outbox_failure_returns_job_to_pending_until_attempt_limit() -> None:
    factory = get_session_factory(settings())
    tender = make_tender()
    try:
        async with factory() as session:
            repository = TenderRepository(session)
            jobs = TenderIngestionJobRepository(session)
            stored = await repository.save(tender, source_object="scrapers/test", commit=False)
            job, _ = await jobs.enqueue(
                stored.tender.id,
                portal_name="vergabe.bayern",
                commit=False,
            )
            await session.commit()
            claimed = await jobs.claim_pending(limit=1, worker_id="live-test")
            assert claimed
            await jobs.fail(job.id, "temporary parser failure", retry_backoff_seconds=0)
            pending = await session.get(TenderIngestionJob, job.id)
            assert pending is not None
            assert pending.status == "pending"
            assert pending.last_error == "temporary parser failure"

        async with factory() as session:
            await session.execute(delete(TenderIngestionJob).where(TenderIngestionJob.id == job.id))
            await session.execute(delete(TenderORM).where(TenderORM.id == tender.id))
            await session.commit()
    finally:
        await dispose_engine()


async def test_retry_and_discard_commit_quarantine_state() -> None:
    factory = get_session_factory(settings())
    retry_tender = make_tender()
    discard_tender = make_tender()
    retry_job = None
    discard_job = None
    try:
        async with factory() as session:
            repository = TenderRepository(session)
            jobs = TenderIngestionJobRepository(session)
            retry_stored = await repository.save(retry_tender, commit=False)
            retry_job, _ = await jobs.enqueue(
                retry_stored.tender.id,
                portal_name="vergabe.bayern",
                commit=False,
            )
            discard_stored = await repository.save(discard_tender, commit=False)
            discard_job, _ = await jobs.enqueue(
                discard_stored.tender.id,
                portal_name="vergabe.bayern",
                commit=False,
            )
            await session.commit()
            await jobs.security_fail(retry_job.id, "Eicar-Test-Signature")
            await jobs.security_fail(discard_job.id, "Eicar-Test-Signature")
            quarantined = await jobs.list_jobs(quarantined=True, limit=10)
            assert {view.id for view in quarantined} == {retry_job.id, discard_job.id}

        async with factory() as session:
            jobs = TenderIngestionJobRepository(session)
            retried = await jobs.retry_failed(retry_job.id)
            assert retried.status == "pending"
            assert retried.attempts == 0
            assert retried.quarantined is False
            assert retried.quarantine_reason is None
            assert retried.last_error is None

            discarded = await jobs.discard(discard_job.id, "closed by operator")
            assert discarded.status == "skipped"
            assert discarded.quarantined is False
            assert discarded.quarantine_reason == "Eicar-Test-Signature"
            assert discarded.last_error == "closed by operator"

        async with factory() as session:
            retry_row = await session.get(TenderIngestionJob, retry_job.id)
            discard_row = await session.get(TenderIngestionJob, discard_job.id)
            assert retry_row is not None
            assert retry_row.status == "pending"
            assert retry_row.quarantined is False
            assert retry_row.quarantine_reason is None
            assert retry_row.last_error is None
            assert discard_row is not None
            assert discard_row.status == "skipped"
            assert discard_row.quarantined is False
            assert discard_row.last_error == "closed by operator"
            await session.execute(delete(TenderIngestionJob).where(TenderIngestionJob.id == retry_job.id))
            await session.execute(delete(TenderIngestionJob).where(TenderIngestionJob.id == discard_job.id))
            await session.execute(delete(TenderORM).where(TenderORM.id == retry_tender.id))
            await session.execute(delete(TenderORM).where(TenderORM.id == discard_tender.id))
            await session.commit()
    finally:
        await dispose_engine()


async def test_stale_processing_locks_are_released() -> None:
    factory = get_session_factory(settings())
    tender = make_tender()
    job = None
    try:
        async with factory() as session:
            repository = TenderRepository(session)
            jobs = TenderIngestionJobRepository(session)
            stored = await repository.save(tender, commit=False)
            job, _ = await jobs.enqueue(
                stored.tender.id,
                portal_name="vergabe.bayern",
                commit=False,
            )
            await session.commit()
            claimed = await jobs.claim_pending(limit=1, worker_id="live-test")
            assert claimed and claimed[0].attempts == 1
            row = await session.get(TenderIngestionJob, job.id)
            assert row is not None
            row.locked_at = datetime.now(UTC) - timedelta(hours=1)
            await session.commit()

        async with factory() as session:
            jobs = TenderIngestionJobRepository(session)
            assert await jobs.release_stale(older_than_seconds=60) == 1

        async with factory() as session:
            released = await session.get(TenderIngestionJob, job.id)
            assert released is not None
            assert released.status == "pending"
            assert released.attempts == 0
            assert released.locked_at is None
            assert released.locked_by is None
            await session.execute(delete(TenderIngestionJob).where(TenderIngestionJob.id == job.id))
            await session.execute(delete(TenderORM).where(TenderORM.id == tender.id))
            await session.commit()
    finally:
        await dispose_engine()


async def test_portal_advisory_lock_is_exclusive_per_portal() -> None:
    current = settings()
    engine = get_engine(current)
    first = DatabasePortalLock(engine)
    second = DatabasePortalLock(engine)
    try:
        assert await first.acquire("vergabe.bayern") is True
        assert await second.acquire("vergabe.bayern") is False

        await first.release("vergabe.bayern")
        assert await second.acquire("vergabe.bayern") is True
    finally:
        await first.release_all()
        await second.release_all()
        await dispose_engine()


async def test_worker_run_history_records_counts_and_duration() -> None:
    factory = get_session_factory(settings())
    try:
        async with factory() as session:
            runs = WorkerRunRepository(session)
            started = await runs.start(
                run_type="scrape",
                resource="vergabe.bayern",
                worker_id="live-test",
            )
            finished = await runs.finish(
                started.id,
                status="succeeded",
                items_processed=4,
                errors_count=1,
                details={"portal": "vergabe.bayern"},
            )
            assert finished.status == "succeeded"
            assert finished.items_processed == 4
            assert finished.errors_count == 1
            assert finished.duration_ms is not None
            assert finished.duration_ms >= 0

            latest = await runs.latest_by_type()
            assert latest["scrape"].id == started.id
            counts = await runs.status_counts()
            assert counts["succeeded"] >= 1

            await session.execute(delete(WorkerRun).where(WorkerRun.id == started.id))
            await session.commit()
    finally:
        await dispose_engine()


async def test_worker_run_retention_deletes_old_runs() -> None:
    factory = get_session_factory(settings())
    old_started = datetime.now(UTC) - timedelta(days=120)
    cutoff = datetime.now(UTC) - timedelta(days=90)
    try:
        async with factory() as session:
            runs = WorkerRunRepository(session)
            old = await runs.start(
                run_type="scrape",
                resource="retention-old",
                worker_id="live-test",
                started_at=old_started,
            )
            fresh = await runs.start(
                run_type="scrape",
                resource="retention-fresh",
                worker_id="live-test",
            )
            assert await runs.count_older_than(cutoff) >= 1
            deleted = await runs.delete_older_than(cutoff)
            assert deleted >= 1

        async with factory() as session:
            assert await session.get(WorkerRun, old.id) is None
            assert await session.get(WorkerRun, fresh.id) is not None
            await session.execute(delete(WorkerRun).where(WorkerRun.id == fresh.id))
            await session.commit()
    finally:
        await dispose_engine()


async def test_worker_command_guard_lifecycle_and_cancellation() -> None:
    factory = get_session_factory(settings())
    try:
        async with factory() as session:
            commands = WorkerCommandRepository(session)
            queued = await commands.create(
                command_type="scrape",
                requested_by="live-test",
                portal="all",
                max_pages=1,
            )
            with pytest.raises(WorkerCommandConflict):
                await commands.create(
                    command_type="scrape",
                    requested_by="live-test",
                    portal="all",
                    max_pages=1,
                )
            assert await commands.mark_running(queued.id, 12345) is True
            assert await commands.finish(queued.id, status="succeeded") is True
            assert await commands.get(queued.id) is not None
            assert (await commands.get(queued.id)).status == "succeeded"  # type: ignore[union-attr]

            cancellable = await commands.create(
                command_type="ingestion",
                requested_by="live-test",
                batch_limit=5,
            )
            cancelled = await commands.cancel(cancellable.id, "maintenance")
            assert cancelled.status == "cancelled"
            assert await commands.finish(cancellable.id, status="succeeded") is False

            await session.execute(
                delete(WorkerCommand).where(WorkerCommand.id.in_((queued.id, cancellable.id)))
            )
            await session.commit()
    finally:
        await dispose_engine()
