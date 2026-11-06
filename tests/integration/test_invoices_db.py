"""Live PostgreSQL tests for gapless invoice numbering and VAT persistence."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.generators.xrechnung_generator import XRechnungGenerator
from src.models.enums import CustomerOfferStatus, UnitOfMeasure
from src.models.orm import (
    CustomerOffer as CustomerOfferORM,
    CustomerOfferLine as CustomerOfferLineORM,
    Invoice as InvoiceORM,
    InvoiceLine as InvoiceLineORM,
    InvoiceNumberSequence,
    InvoiceVatSplit,
)
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.repositories.invoice_repository import InvoiceRepository
from src.repositories.offer_repository import OfferRepository

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)

YEAR = 2099
NOW = datetime(2099, 1, 15, 9, 0, tzinfo=timezone.utc)


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="live-test-jwt-secret-with-at-least-32-bytes",
        api_admin_password="test-admin-password",
    )


def make_offer() -> CustomerOffer:
    line = CustomerOfferLine(
        position_number="1.1",
        description="Nitrilhandschuhe blau Gr. M",
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
        offer_number=f"P44-{uuid4().hex[:12]}",
        customer_name="Stadt Muster",
        customer_contact_email="einkauf@musterstadt.de",
        status=CustomerOfferStatus.APPROVED,
        lines=[line],
        margin_rate=Decimal("0.15"),
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2099, 12, 31),
        ed25519_approval_signature="a" * 128,
        approval_payload_hash="a" * 64,
        approved_by="C. Mueller",
        approved_at=NOW,
    )


def make_invoice(*, duplicate_line: bool = False) -> Invoice:
    lines = [
        InvoiceLine(
            line_id=1,
            name="Nitrilhandschuhe",
            quantity=Decimal("10"),
            unit=UnitOfMeasure.STUECK,
            unit_price=Decimal("10.00"),
            line_total=Decimal("100.00"),
            vat_rate=Decimal("0.19"),
        ),
        InvoiceLine(
            line_id=2,
            name="Papierhandtücher",
            quantity=Decimal("2"),
            unit=UnitOfMeasure.PACK,
            unit_price=Decimal("50.00"),
            line_total=Decimal("100.00"),
            vat_rate=Decimal("0.07"),
        ),
        InvoiceLine(
            line_id=3,
            name="Pflegeleistung",
            quantity=Decimal("1"),
            unit=UnitOfMeasure.STUECK,
            unit_price=Decimal("50.00"),
            line_total=Decimal("50.00"),
            vat_rate=Decimal("0"),
        ),
    ]
    if duplicate_line:
        lines.append(
            InvoiceLine(
                line_id=3,
                name="Doppelte Position",
                quantity=Decimal("1"),
                unit=UnitOfMeasure.STUECK,
                unit_price=Decimal("50.00"),
                line_total=Decimal("50.00"),
                vat_rate=Decimal("0"),
            )
        )
    net = q2(sum((line.line_total for line in lines), start=Decimal("0.00")))
    vat = q2(
        sum(
            (
                q2(
                    sum(
                        (line.line_total for line in lines if line.vat_rate == rate),
                        start=Decimal("0.00"),
                    )
                    * rate
                )
                for rate in {line.vat_rate for line in lines}
            ),
            start=Decimal("0.00"),
        )
    )
    return Invoice(
        invoice_number="PENDING",
        issue_date=date(YEAR, 1, 15),
        due_date=date(YEAR, 2, 14),
        delivery_date=date(YEAR, 1, 15),
        seller=InvoiceParty(
            name="BayerClean",
            street="Reithmayrstraße 52",
            city="Regensburg",
            postal_code="93055",
            vat_id="DE123456789",
            email="rechnung@bayerclean.de",
            contact_name="Buchhaltung",
            telephone="+49 941 123456",
        ),
        buyer=InvoiceParty(
            name="Stadt Muster",
            street="Rathausstr. 2",
            city="Musterstadt",
            postal_code="12345",
            email="einkauf@musterstadt.de",
        ),
        buyer_reference="991-03730-19",
        lines=lines,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        iban="DE89370400440532013000",
    )


async def _cleanup(session) -> None:  # type: ignore[no-untyped-def]
    offer_ids = (
        await session.execute(
            text("select id from customer_offers where offer_number like 'P44-%'")
        )
    ).scalars().all()
    for offer_id in offer_ids:
        invoice_ids = (
            await session.execute(
                text("select id from invoices where offer_id = :offer_id"),
                {"offer_id": offer_id},
            )
        ).scalars().all()
        for invoice_id in invoice_ids:
            await session.execute(
                delete(InvoiceVatSplit).where(InvoiceVatSplit.invoice_id == invoice_id)
            )
            await session.execute(
                delete(InvoiceLineORM).where(InvoiceLineORM.invoice_id == invoice_id)
            )
            await session.execute(delete(InvoiceORM).where(InvoiceORM.id == invoice_id))
        await session.execute(
            delete(CustomerOfferLineORM).where(CustomerOfferLineORM.offer_id == offer_id)
        )
        await session.execute(delete(CustomerOfferORM).where(CustomerOfferORM.id == offer_id))
    await session.execute(
        delete(InvoiceNumberSequence).where(InvoiceNumberSequence.year == YEAR)
    )
    await session.commit()


async def test_invoice_sequence_is_gapless_after_rolled_back_insert() -> None:
    factory = get_session_factory(settings())
    try:
        async with factory() as session:
            await _cleanup(session)
            offers = OfferRepository(session)
            invoices = InvoiceRepository(session)

            first_offer = (await offers.save(make_offer())).offer
            first = await invoices.create(make_invoice(), offer_id=first_offer.id)
            assert first.invoice.invoice_number == "INV-2099-0001"

            second_offer = (await offers.save(make_offer())).offer
            with pytest.raises(Exception):
                await invoices.create(make_invoice(duplicate_line=True), offer_id=second_offer.id)

            retry = await invoices.create(make_invoice(), offer_id=second_offer.id)
            assert retry.invoice.invoice_number == "INV-2099-0002"
            assert [line.line_id for line in retry.invoice.lines] == [1, 2, 3]
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_invoice_records_credit_status_and_note_number() -> None:
    factory = get_session_factory(settings())
    try:
        async with factory() as session:
            await _cleanup(session)
            offer = (await OfferRepository(session).save(make_offer())).offer
            stored = await InvoiceRepository(session).create(make_invoice(), offer_id=offer.id)
            repository = InvoiceRepository(session)

            partial = await repository.record_credit(
                stored.id,
                Decimal("100.00"),
                credit_note_number="GS-2099-0001",
            )
            assert partial.status.value == "PARTIALLY_CREDITED"
            assert partial.credit_note_number == "GS-2099-0001"

            full = await repository.record_credit(
                stored.id,
                Decimal("176.00"),
                credit_note_number="GS-2099-0001",
            )
            assert full.status.value == "CREDITED"
            assert full.credited_amount == Decimal("276.00")
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_invoice_persists_multi_rate_vat_splits_and_payment_status() -> None:
    factory = get_session_factory(settings())
    try:
        async with factory() as session:
            await _cleanup(session)
            offer = (await OfferRepository(session).save(make_offer())).offer
            stored = await InvoiceRepository(session).create(make_invoice(), offer_id=offer.id)

            assert stored.invoice.total_net == Decimal("250.00")
            assert stored.invoice.total_vat == Decimal("26.00")
            assert stored.invoice.seller.telephone == "+49 941 123456"
            xml = XRechnungGenerator().build(stored.invoice)
            assert b"INV-2099-" in xml
            assert stored.vat_breakdown == (
                {"rate": "0.19", "taxable_amount": "100.00", "tax_amount": "19.00"},
                {"rate": "0.07", "taxable_amount": "100.00", "tax_amount": "7.00"},
                {"rate": "0", "taxable_amount": "50.00", "tax_amount": "0.00"},
            )

            split_rows = (
                await session.execute(
                    text(
                        "select rate, taxable_amount, tax_amount from invoice_vat_splits "
                        "where invoice_id = :invoice_id order by rate desc"
                    ),
                    {"invoice_id": stored.id},
                )
            ).all()
            assert [(row[0], row[1], row[2]) for row in split_rows] == [
                (Decimal("0.1900"), Decimal("100.00"), Decimal("19.00")),
                (Decimal("0.0700"), Decimal("100.00"), Decimal("7.00")),
                (Decimal("0.0000"), Decimal("50.00"), Decimal("0.00")),
            ]

            paid = await InvoiceRepository(session).record_payment(stored.id, Decimal("100.00"))
            assert paid.status.value == "PARTIALLY_PAID"
            assert paid.paid_amount == Decimal("100.00")
            full = await InvoiceRepository(session).record_payment(stored.id, Decimal("176.00"))
            assert full.status.value == "PAID"
            assert full.paid_at is not None
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()
