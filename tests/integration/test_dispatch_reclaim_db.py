"""Live PostgreSQL tests for stale n8n dispatch reservation reclaim."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.models.orm import IntegrationIdempotency
from src.repositories.integration_repository import IntegrationRepository

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="dispatch-reclaim-secret-32-bytes",
        api_admin_password="test-admin-password",
    )


async def _cleanup(session, key: str) -> None:  # type: ignore[no-untyped-def]
    await session.execute(delete(IntegrationIdempotency).where(IntegrationIdempotency.key == key))
    await session.commit()


async def test_stale_pending_reservation_is_reclaimed_for_same_request() -> None:
    current = settings()
    factory = get_session_factory(current)
    key = f"test-dispatch-{uuid4().hex}"
    try:
        async with factory() as session:
            repository = IntegrationRepository(session)
            record, created = await repository.reserve(
                key=key,
                operation="rfq.dispatch",
                request_hash="a" * 64,
            )
            assert created is True
            assert record.status == "pending"
            await _cleanup(session, key)
    finally:
        await dispose_engine()

    factory = get_session_factory(current)
    try:
        async with factory() as session:
            repository = IntegrationRepository(session)
            await repository.reserve(
                key=key,
                operation="rfq.dispatch",
                request_hash="a" * 64,
            )
            await session.execute(
                update(IntegrationIdempotency)
                .where(IntegrationIdempotency.key == key)
                .values(updated_at=datetime.now(UTC) - timedelta(hours=2))
            )
            await session.commit()

            fresh, created = await repository.reserve(
                key=key,
                operation="rfq.dispatch",
                request_hash="a" * 64,
                stale_after_seconds=900,
            )
            assert created is True
            assert fresh.status == "pending"

            different_body, created = await repository.reserve(
                key=key,
                operation="rfq.dispatch",
                request_hash="b" * 64,
                stale_after_seconds=900,
            )
            assert created is False
            assert different_body.request_hash == "a" * 64
    finally:
        async with get_session_factory(current)() as session:
            await _cleanup(session, key)
        await dispose_engine()


async def test_cleanup_marks_old_pending_rows_failed() -> None:
    current = settings()
    key = f"test-dispatch-{uuid4().hex}"
    try:
        async with get_session_factory(current)() as session:
            repository = IntegrationRepository(session)
            await repository.reserve(
                key=key,
                operation="rfq.dispatch",
                request_hash="c" * 64,
            )
            await session.execute(
                update(IntegrationIdempotency)
                .where(IntegrationIdempotency.key == key)
                .values(updated_at=datetime.now(UTC) - timedelta(hours=2))
            )
            await session.commit()

            dry_run = await repository.reclaim_stale_pending(
                max_age_seconds=900,
                operation="rfq.dispatch",
                dry_run=True,
            )
            assert dry_run >= 1

            reclaimed = await repository.reclaim_stale_pending(
                max_age_seconds=900,
                operation="rfq.dispatch",
            )
            assert reclaimed >= 1
            await session.commit()
            row = await session.get(IntegrationIdempotency, key)
            assert row is not None
            assert row.status == "failed"
    finally:
        async with get_session_factory(current)() as session:
            await _cleanup(session, key)
        await dispose_engine()
