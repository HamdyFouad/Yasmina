"""Live PostgreSQL billing tests: credit notes, GoBD events and immutability."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.models.enums import CustomerOfferStatus, InvoiceStatus, UnitOfMeasure
from src.models.orm import (
    CreditNote,
    CreditNoteNumberSequence,
    InvoiceEvent,
    InvoiceNumberSequence,
    InvoiceVatSplit,
)
from src.models.orm import (
    CustomerOffer as CustomerOfferORM,
)
from src.models.orm import (
    CustomerOfferLine as CustomerOfferLineORM,
)
from src.models.orm import (
    Invoice as InvoiceORM,
)
from src.models.orm import (
    InvoiceLine as InvoiceLineORM,
)
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.repositories.credit_note_repository import CreditNoteRepository
from src.repositories.invoice_repository import InvoiceRepository
from src.repositories.offer_repository import OfferRepository

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)

YEAR = 2099
NOW = datetime(2099, 1, 15, 9, 0, tzinfo=UTC)


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
        offer_number=f"P47-{uuid4().hex[:12]}",
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


def make_invoice() -> Invoice:
    return Invoice(
        invoice_number="PENDING",
        issue_date=date(YEAR, 1, 15),
        due_date=date(YEAR, 2, 14),
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
        lines=[
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
        ],
        total_net=Decimal("200.00"),
        total_vat=Decimal("26.00"),
        total_gross=Decimal("226.00"),
        iban="DE89370400440532013000",
    )


async def _cleanup(session) -> None:  # type: ignore[no-untyped-def]
    offer_ids = (
        (
            await session.execute(
                select(CustomerOfferORM.id).where(CustomerOfferORM.offer_number.like("P47-%"))
            )
        )
        .scalars()
        .all()
    )
    for offer_id in offer_ids:
        invoice_ids = (
            (await session.execute(select(InvoiceORM.id).where(InvoiceORM.offer_id == offer_id)))
            .scalars()
            .all()
        )
        for invoice_id in invoice_ids:
            await session.execute(
                text("ALTER TABLE invoice_events DISABLE TRIGGER trg_invoice_events_append_only")
            )
            await session.execute(delete(InvoiceEvent).where(InvoiceEvent.invoice_id == invoice_id))
            await session.execute(
                text("ALTER TABLE invoice_events ENABLE TRIGGER trg_invoice_events_append_only")
            )
            await session.execute(delete(CreditNote).where(CreditNote.invoice_id == invoice_id))
            await session.execute(delete(InvoiceVatSplit).where(InvoiceVatSplit.invoice_id == invoice_id))
            await session.execute(delete(InvoiceLineORM).where(InvoiceLineORM.invoice_id == invoice_id))
            await session.execute(delete(InvoiceORM).where(InvoiceORM.id == invoice_id))
        await session.execute(delete(CustomerOfferLineORM).where(CustomerOfferLineORM.offer_id == offer_id))
        await session.execute(delete(CustomerOfferORM).where(CustomerOfferORM.id == offer_id))
    await session.execute(delete(InvoiceNumberSequence).where(InvoiceNumberSequence.year == YEAR))
    await session.execute(delete(CreditNoteNumberSequence).where(CreditNoteNumberSequence.year == YEAR))
    await session.commit()


async def test_credit_notes_gaplessly_numbered_and_events_hash_chained() -> None:
    factory = get_session_factory(settings())
    try:
        async with factory() as session:
            await _cleanup(session)
            offer = (await OfferRepository(session).save(make_offer())).offer
            invoice_repository = InvoiceRepository(session)
            invoice = await invoice_repository.create(
                make_invoice(),
                offer_id=offer.id,
                actor="billing-test",
            )
            assert invoice.invoice.invoice_number == "INV-2099-0001"
            assert invoice.status is InvoiceStatus.ISSUED
            events = await invoice_repository.events(invoice.id)
            assert [event["event_type"] for event in events] == ["ISSUED"]
            assert await invoice_repository.verify_event_chain(invoice.id) is True

            paid = await invoice_repository.record_payment(
                invoice.id,
                Decimal("50.00"),
                actor="billing-test",
                reference="BANK-1",
            )
            assert paid.status is InvoiceStatus.PARTIALLY_PAID

            credits = CreditNoteRepository(session)
            note = await credits.issue(
                invoice.id,
                amount=Decimal("26.00"),
                reason="Retoure",
                issue_date=date(YEAR, 3, 1),
                actor="billing-test",
            )
            assert note.credit_note_number == "GS-2099-0001"
            assert note.total_gross == Decimal("26.00")
            assert note.total_net + note.total_vat == note.total_gross

            refreshed = await invoice_repository.get_by_id(invoice.id)
            assert refreshed is not None
            assert refreshed.credited_amount == Decimal("26.00")
            assert refreshed.status is InvoiceStatus.PARTIALLY_CREDITED
            event_types = [event["event_type"] for event in await invoice_repository.events(invoice.id)]
            assert event_types == ["ISSUED", "PAYMENT_RECORDED", "CREDIT_ISSUED"]
            assert await invoice_repository.verify_event_chain(invoice.id) is True

            credit_notes = await credits.list_for_invoice(invoice.id)
            assert [entry.credit_note_number for entry in credit_notes] == ["GS-2099-0001"]

            with pytest.raises(DBAPIError):
                await session.execute(
                    text("UPDATE invoice_events SET actor = 'tamper' WHERE invoice_id = :invoice_id"),
                    {"invoice_id": invoice.id},
                )
            await session.rollback()
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()
