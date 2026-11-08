"""Live PostgreSQL test for the quotation sync outbox lease/backoff semantics."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.models.enums import CustomerOfferStatus, UnitOfMeasure
from src.models.orm import CustomerOffer as CustomerOfferORM
from src.models.orm import CustomerOfferLine as CustomerOfferLineORM
from src.models.orm import QuotationSyncJob
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.repositories.offer_repository import OfferRepository
from src.repositories.quotation_sync_repository import QuotationSyncJobRepository

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
        jwt_secret_key="quotation-sync-db-secret-32-bytes",
        api_admin_password="test-admin-password",
    )


def make_offer(number: str) -> CustomerOffer:
    line = CustomerOfferLine(
        position_number="1.1",
        description="Nitrilhandschuhe blau",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
        cost_unit_price_net=Decimal("10.0000"),
        margin_rate=Decimal("0.15"),
        unit_price_net=q4(Decimal("10.0000") * Decimal("1.15")),
        total_net=q2(Decimal("10.0000") * q4(Decimal("10.0000") * Decimal("1.15"))),
    )
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    return CustomerOffer(
        offer_number=number,
        customer_name="Stadt Muster",
        status=CustomerOfferStatus.APPROVED,
        lines=[line],
        margin_rate=Decimal("0.15"),
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
        ed25519_approval_signature="a" * 128,
        approval_payload_hash="a" * 64,
        approved_by="C. Mueller",
        approved_at=datetime.now(UTC),
    )


async def _cleanup(session, offer_number: str) -> None:  # type: ignore[no-untyped-def]
    offer_id = (
        (
            await session.execute(
                select(CustomerOfferORM.id).where(CustomerOfferORM.offer_number == offer_number)
            )
        )
        .scalars()
        .first()
    )
    if offer_id is not None:
        await session.execute(delete(QuotationSyncJob).where(QuotationSyncJob.offer_id == offer_id))
        await session.execute(delete(CustomerOfferLineORM).where(CustomerOfferLineORM.offer_id == offer_id))
        await session.execute(delete(CustomerOfferORM).where(CustomerOfferORM.id == offer_id))
    await session.commit()


async def test_enqueue_claim_backoff_and_complete() -> None:
    current_settings = settings()
    number = f"P64-{uuid4().hex[:12]}"
    factory = get_session_factory(current_settings)
    try:
        async with factory() as session:
            await _cleanup(session, number)
            offer = (await OfferRepository(session).save(make_offer(number))).offer
            jobs = QuotationSyncJobRepository(session)

            first, created = await jobs.enqueue(
                offer.id,
                offer_number=number,
                max_attempts=3,
            )
            replay, created_again = await jobs.enqueue(
                offer.id,
                offer_number=number,
                max_attempts=3,
            )
            assert created is True
            assert created_again is False
            assert replay.id == first.id

            claimed = await jobs.claim_pending(limit=10, worker_id="test-worker")
            assert len(claimed) == 1
            claimed_job = claimed[0]
            assert claimed_job.attempts == 1

            after_fail = await jobs.fail(
                claimed_job.id,
                "ERPNext 503",
                retry_backoff_seconds=120,
            )
            assert after_fail == "pending"
            row = await session.get(QuotationSyncJob, claimed_job.id)
            assert row is not None
            assert row.available_at > datetime.now(UTC) + timedelta(seconds=60)

            reclaimed = await jobs.claim_pending(
                limit=10,
                worker_id="test-worker",
                now=row.available_at + timedelta(seconds=1),
            )
            assert len(reclaimed) == 1
            assert reclaimed[0].attempts == 2

            await jobs.complete(reclaimed[0].id, quotation_name="QTN-2026-0001")
            completed = await jobs.get_by_offer(offer.id)
            assert completed is not None
            assert completed.status == "succeeded"
            assert completed.quotation_name == "QTN-2026-0001"
            assert completed.attempts == 2

            await _cleanup(session, number)
    finally:
        await dispose_engine()
