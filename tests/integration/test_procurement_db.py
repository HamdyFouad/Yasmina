"""Live PostgreSQL procurement lifecycle: accept quote, PO, invoice and margin."""

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
    ProcurementProcedure,
    PurchaseOrderStatus,
    SupplierInvoiceStatus,
    SupplierQuoteStatus,
    TenderSourceFormat,
    TenderStatus,
    UnitOfMeasure,
)
from src.models.orm import (
    CustomerOffer,
    CustomerOfferLine,
    PurchaseOrder,
    Supplier,
    SupplierInvoice,
    SupplierQuote,
    SupplierQuoteLine,
    Tender,
)
from src.repositories.procurement_repository import (
    ProcurementRepository,
    SupplierInvoiceInput,
    SupplierInvoiceLineInput,
)
from src.repositories.supplier_quote_repository import SupplierQuoteRepository

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
        jwt_secret_key="procurement-live-secret-with-32-bytes",
        api_admin_password="test-admin-password",
    )


async def test_purchase_order_invoice_matching_and_margin_lifecycle() -> None:
    suffix = datetime.now(UTC).strftime("%H%M%S%f")
    factory = get_session_factory(settings())
    today = date.today()
    supplier_id = uuid4()
    tender_id = uuid4()
    offer_id = uuid4()
    quote_id = uuid4()
    try:
        async with factory() as session:
            supplier = Supplier(
                id=supplier_id,
                name=f"PROC-SUP-{suffix}",
                email="procurement@example.de",
                country_code="DE",
            )
            tender = Tender(
                id=tender_id,
                tender_number=f"PROC-T-{suffix}",
                title="Procurement lifecycle test",
                client_name="Stadt Muster",
                procedure=ProcurementProcedure.OPEN_PROCEDURE,
                source_format=TenderSourceFormat.GAEB_X83,
                submission_deadline=datetime.now(UTC) + timedelta(days=30),
                status=TenderStatus.PUBLISHED,
            )
            offer = CustomerOffer(
                id=offer_id,
                offer_number=f"PROC-O-{suffix}",
                customer_name="Stadt Muster",
                tender_id=tender_id,
                status=CustomerOfferStatus.APPROVED,
                currency=Currency.EUR,
                margin_rate=Decimal("0.4000"),
                total_net=Decimal("1000.00"),
                vat_rate=Decimal("0.1900"),
                total_vat=Decimal("190.00"),
                total_gross=Decimal("1190.00"),
                valid_until=today + timedelta(days=14),
                ed25519_approval_signature="a" * 128,
                approval_payload_hash="b" * 64,
                approved_by="admin",
                approved_at=datetime.now(UTC),
                owner="sales",
                lines=[
                    CustomerOfferLine(
                        position_number="1",
                        description="Nitrilhandschuhe",
                        quantity=Decimal("10.0000"),
                        unit=UnitOfMeasure.KARTON,
                        cost_unit_price_net=Decimal("60.0000"),
                        margin_rate=Decimal("0.4000"),
                        unit_price_net=Decimal("100.0000"),
                        total_net=Decimal("1000.00"),
                        vat_rate=Decimal("0.1900"),
                    )
                ],
            )
            quote = SupplierQuote(
                id=quote_id,
                supplier_id=supplier_id,
                supplier_name=supplier.name,
                supplier_contact_email=supplier.email,
                quote_reference=f"PROC-Q-{suffix}",
                tender_id=tender_id,
                status=SupplierQuoteStatus.VALIDATED,
                currency=Currency.EUR,
                delivery_days=5,
                total_net=Decimal("200.00"),
                lines=[
                    SupplierQuoteLine(
                        supplier_quote_id=quote_id,
                        line_number=1,
                        description="Nitrilhandschuhe",
                        quantity=Decimal("10.0000"),
                        unit=UnitOfMeasure.KARTON,
                        unit_price_net=Decimal("20.0000"),
                    )
                ],
            )
            session.add_all([supplier, tender])
            await session.flush()
            session.add_all([offer, quote])
            await session.commit()

        async with factory() as session:
            quotes = SupplierQuoteRepository(session)
            accepted = await quotes.accept(quote_id, actor="admin")
            assert accepted.status == SupplierQuoteStatus.ACCEPTED
            assert all(line.is_accepted for line in accepted.lines)

            procurement = ProcurementRepository(session)
            po = await procurement.create_purchase_order_from_quote(
                quote_id,
                requested_by="admin",
                customer_offer_id=offer_id,
                expected_delivery_date=today + timedelta(days=5),
            )
            assert po.status == "DRAFT"
            assert po.po_number.startswith("PO-")
            assert po.total_net == Decimal("200.00")
            assert len(po.lines) == 1

            sent = await procurement.transition_purchase_order(po.id, PurchaseOrderStatus.SENT)
            assert sent.status == "SENT"
            assert sent.sent_at is not None
            confirmed = await procurement.transition_purchase_order(po.id, PurchaseOrderStatus.CONFIRMED)
            assert confirmed.status == "CONFIRMED"
            delivered = await procurement.transition_purchase_order(po.id, PurchaseOrderStatus.DELIVERED)
            assert delivered.status == "DELIVERED"
            assert delivered.lines[0].received_quantity == Decimal("10.0000")

            exact = await procurement.create_supplier_invoice(
                SupplierInvoiceInput(
                    purchase_order_id=po.id,
                    invoice_number=f"PROC-INV-{suffix}",
                    invoice_date=today,
                    delivery_date=today + timedelta(days=4),
                    total_net=Decimal("200.00"),
                    vat_rate=Decimal("0.1900"),
                    lines=(
                        SupplierInvoiceLineInput(
                            purchase_order_line_id=po.lines[0].id,
                            quantity=Decimal("10.0000"),
                            unit_price_net=Decimal("20.0000"),
                        ),
                    ),
                ),
                received_by="admin",
            )
            assert exact.status == "MATCHED"
            assert exact.total_gross == Decimal("238.00")
            assert exact.exception_codes == ()

            margin = await procurement.get_margin_for_offer(offer_id)
            assert margin is not None
            assert margin.quoted_margin_rate == Decimal("0.4000")
            assert margin.actual_margin_rate == Decimal("0.8000")
            assert margin.margin_variance_rate == Decimal("0.4000")
            assert margin.invoice_count == 1

            exception = await procurement.create_supplier_invoice(
                SupplierInvoiceInput(
                    purchase_order_id=po.id,
                    invoice_number=f"PROC-INV-EX-{suffix}",
                    invoice_date=today,
                    delivery_date=today + timedelta(days=9),
                    total_net=Decimal("210.00"),
                    lines=(
                        SupplierInvoiceLineInput(
                            purchase_order_line_id=po.lines[0].id,
                            quantity=Decimal("10.0000"),
                            unit_price_net=Decimal("21.0000"),
                        ),
                    ),
                ),
                received_by="admin",
            )
            assert exception.status == SupplierInvoiceStatus.EXCEPTION.value
            assert {"TOTAL_MISMATCH", "PRICE_MISMATCH", "DELIVERY_LATE"} <= set(exception.exception_codes)

            rematched = await procurement.rematch_supplier_invoice(exception.id)
            assert rematched.status == "EXCEPTION"

            po_id = po.id
            invoice_ids = (exact.id, exception.id)

        async with factory() as session:
            await session.execute(delete(SupplierInvoice).where(SupplierInvoice.id.in_(invoice_ids)))
            await session.execute(delete(PurchaseOrder).where(PurchaseOrder.id == po_id))
            await session.execute(delete(SupplierQuote).where(SupplierQuote.id == quote_id))
            await session.execute(delete(CustomerOffer).where(CustomerOffer.id == offer_id))
            await session.execute(delete(Tender).where(Tender.id == tender_id))
            await session.execute(delete(Supplier).where(Supplier.id == supplier_id))
            await session.commit()
    finally:
        await dispose_engine()
