"""Live PostgreSQL reporting aggregate tests."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.models.enums import (
    Currency,
    CustomerOfferStatus,
    InvoiceStatus,
    ProcurementProcedure,
    TenderSourceFormat,
    TenderStatus,
    UnitOfMeasure,
)
from src.models.orm import CustomerOffer, CustomerOfferLine, Invoice, Tender
from src.repositories.reporting_repository import ReportingRepository

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
        jwt_secret_key="reporting-live-secret-with-32-bytes",
        api_admin_password="test-admin-password",
    )


def offer(
    *,
    offer_id,
    tender_id,
    status: CustomerOfferStatus,
    total_net: Decimal,
    cost: Decimal,
) -> CustomerOffer:
    return CustomerOffer(
        id=offer_id,
        offer_number=f"REP-{str(offer_id)[:8]}",
        customer_name="Stadt Muster",
        tender_id=tender_id,
        status=status,
        currency=Currency.EUR,
        margin_rate=Decimal("0.4000"),
        total_net=total_net,
        vat_rate=Decimal("0.1900"),
        total_vat=Decimal("190.00"),
        total_gross=total_net + Decimal("190.00"),
        valid_until=date.today() + timedelta(days=14),
        ed25519_approval_signature="a" * 128 if status == CustomerOfferStatus.ACCEPTED else None,
        approval_payload_hash="b" * 64 if status == CustomerOfferStatus.ACCEPTED else None,
        approved_by="admin" if status == CustomerOfferStatus.ACCEPTED else None,
        approved_at=datetime.now(UTC) if status == CustomerOfferStatus.ACCEPTED else None,
        owner="sales",
        lines=[
            CustomerOfferLine(
                position_number="1",
                description="Nitrilhandschuhe",
                quantity=Decimal("10.0000"),
                unit=UnitOfMeasure.KARTON,
                cost_unit_price_net=cost,
                margin_rate=Decimal("0.4000"),
                unit_price_net=Decimal("100.0000"),
                total_net=total_net,
                vat_rate=Decimal("0.1900"),
            )
        ],
    )


def invoice(
    *,
    offer_id,
    number: int,
    status: InvoiceStatus,
    total_net: Decimal,
    paid_amount: Decimal,
    due_date: date,
) -> Invoice:
    vat = (total_net * Decimal("0.19")).quantize(Decimal("0.01"))
    return Invoice(
        offer_id=offer_id,
        invoice_number=f"INV-2026-{number:04d}",
        invoice_year=2026,
        sequence_number=number,
        status=status,
        currency=Currency.EUR,
        issue_date=date(2026, 1, 1),
        due_date=due_date,
        seller_name="Yasmina GmbH",
        seller_street="Hauptstr. 1",
        seller_city="Berlin",
        seller_postal_code="10115",
        buyer_name="Stadt Muster",
        buyer_street="Rathausplatz 1",
        buyer_city="Regensburg",
        buyer_postal_code="93047",
        buyer_email="buyer@example.de",
        total_net=total_net,
        total_vat=vat,
        total_gross=total_net + vat,
        paid_amount=paid_amount,
        credited_amount=Decimal("0.00"),
    )


async def test_reporting_aggregates_pipeline_margin_and_aging() -> None:
    factory = get_session_factory(settings())
    today = date.today()
    tender_id = uuid4()
    accepted_id = uuid4()
    rejected_id = uuid4()
    suffix = datetime.now(UTC).strftime("%H%M%S")
    sequence = 7000 + (int(suffix) % 2000)
    try:
        async with factory() as session:
            tender = Tender(
                id=tender_id,
                tender_number=f"REP-T-{suffix}",
                title="Reporting aggregate test",
                client_name="Stadt Muster",
                procedure=ProcurementProcedure.OPEN_PROCEDURE,
                source_format=TenderSourceFormat.GAEB_X83,
                submission_deadline=datetime.now(UTC) + timedelta(days=30),
                status=TenderStatus.PUBLISHED,
                owner="sales",
            )
            accepted = offer(
                offer_id=accepted_id,
                tender_id=tender_id,
                status=CustomerOfferStatus.ACCEPTED,
                total_net=Decimal("1000.00"),
                cost=Decimal("60.0000"),
            )
            rejected = offer(
                offer_id=rejected_id,
                tender_id=tender_id,
                status=CustomerOfferStatus.REJECTED,
                total_net=Decimal("500.00"),
                cost=Decimal("50.0000"),
            )
            session.add(tender)
            await session.flush()
            session.add_all([accepted, rejected])
            await session.flush()
            session.add_all(
                [
                    invoice(
                        offer_id=accepted_id,
                        number=sequence,
                        status=InvoiceStatus.PAID,
                        total_net=Decimal("1000.00"),
                        paid_amount=Decimal("1190.00"),
                        due_date=today - timedelta(days=45),
                    ),
                    invoice(
                        offer_id=rejected_id,
                        number=sequence + 1,
                        status=InvoiceStatus.OVERDUE,
                        total_net=Decimal("500.00"),
                        paid_amount=Decimal("0.00"),
                        due_date=today - timedelta(days=20),
                    ),
                ]
            )
            await session.commit()

        async with factory() as session:
            repository = ReportingRepository(session)
            summary = await repository.summary()
            margins = await repository.margins()
            aging = await repository.aging(today=today)

            assert summary.win_loss.won_count == 1
            assert summary.win_loss.lost_count == 1
            assert summary.win_loss.win_rate == Decimal("0.5000")
            assert summary.conversion.total_tenders == 1
            assert summary.conversion.tenders_with_offers == 1
            assert summary.conversion.offer_conversion_rate == Decimal("0.5000")
            assert summary.conversion.tender_win_rate == Decimal("1.0000")
            accepted_stage = next(stage for stage in summary.pipeline if stage.status == "ACCEPTED")
            assert accepted_stage.count == 1
            assert accepted_stage.total_net == Decimal("1000.00")

            customer = next(row for row in margins["customers"] if row.label == "Stadt Muster")
            assert customer.revenue_net == Decimal("1500.00")
            assert customer.cost_net == Decimal("1100.00")
            assert customer.margin_net == Decimal("400.00")

            assert aging.paid_total == Decimal("1190.00")
            assert aging.overdue_total == Decimal("595.00")
            assert aging.outstanding_total == Decimal("595.00")
            assert any(bucket.label == "1–30 days" for bucket in aging.buckets)
            assert any(bucket.label == "31–60 days" for bucket in aging.buckets)

        async with factory() as session:
            await session.execute(delete(Invoice).where(Invoice.offer_id.in_((accepted_id, rejected_id))))
            await session.execute(
                delete(CustomerOffer).where(CustomerOffer.id.in_((accepted_id, rejected_id)))
            )
            await session.execute(delete(Tender).where(Tender.id == tender_id))
            await session.commit()
    finally:
        await dispose_engine()
