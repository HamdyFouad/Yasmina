"""Live PostgreSQL tests for the ingestion outbox and scraper watermark."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from src.config import Settings
from src.db import dispose_engine, get_engine, get_session_factory
from src.models.enums import ProcurementProcedure, TenderSourceFormat
from src.models.orm import ScraperState, TenderIngestionJob, WorkerRun
from src.models.orm import Tender as TenderORM
from src.models.schemas import Tender
from src.repositories.ingestion_repository import (
    ScraperStateRepository,
    TenderIngestionJobRepository,
)
from src.repositories.tender_repository import TenderRepository
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
        title="Rahmenvertrag Hygiene",
        client_name="Stadt Muster",
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
