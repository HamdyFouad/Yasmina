"""Credit-note PDF/XML generation tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.models.enums import Currency, InvoiceStatus, UnitOfMeasure
from src.repositories.credit_note_repository import StoredCreditNote
from src.repositories.invoice_repository import StoredInvoice
from src.services.credit_note_documents import (
    build_credit_note_invoice,
    generate_credit_note_documents,
)


def make_original() -> StoredInvoice:
    line = InvoiceLine(
        line_id=1,
        name="Nitrilhandschuhe",
        quantity=Decimal("10"),
        unit=UnitOfMeasure.KARTON,
        unit_price=Decimal("10.00"),
        line_total=Decimal("100.00"),
        vat_rate=Decimal("0.19"),
    )
    invoice = Invoice(
        invoice_number="INV-2099-0001",
        issue_date=date(2099, 1, 15),
        due_date=date(2099, 2, 14),
        currency=Currency.EUR,
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
        order_reference="E2E-REF-1",
        lines=[line],
        total_net=Decimal("100.00"),
        total_vat=Decimal("19.00"),
        total_gross=Decimal("119.00"),
        iban="DE89370400440532013000",
    )
    return StoredInvoice(
        id=uuid4(),
        offer_id=uuid4(),
        invoice=invoice,
        status=InvoiceStatus.ISSUED,
        paid_amount=Decimal("0.00"),
        credited_amount=Decimal("0.00"),
        paid_at=None,
        credit_note_number=None,
        vat_breakdown=({"rate": "0.19", "taxable_amount": "100.00", "tax_amount": "19.00"},),
        artifacts={},
    )


def make_credit_note(invoice_id) -> StoredCreditNote:  # type: ignore[no-untyped-def]
    return StoredCreditNote(
        id=uuid4(),
        invoice_id=invoice_id,
        credit_note_number="GS-2099-0001",
        issue_date=date(2099, 2, 1),
        status="APPLIED",
        reason="Retoure",
        total_net=Decimal("10.00"),
        total_vat=Decimal("1.90"),
        total_gross=Decimal("11.90"),
        vat_breakdown=({"rate": "0.19", "taxable_amount": "10.00", "tax_amount": "1.90"},),
        lines=(
            {
                "description": "Korrektur zu Rechnung INV-2099-0001 (19,0 %)",
                "net_amount": "10.00",
                "vat_rate": "0.19",
                "tax_amount": "1.90",
                "gross_amount": "11.90",
            },
        ),
        artifacts={},
    )


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = (data, content_type)
        return object_name


def test_credit_note_document_is_a_credit_note_with_reference() -> None:
    original = make_original()
    note = make_credit_note(original.id)

    document = build_credit_note_invoice(note, original)

    assert document.invoice_number == "GS-2099-0001"
    assert document.document_type_code == "381"
    assert document.preceding_invoice_number == "INV-2099-0001"
    assert document.preceding_invoice_date == date(2099, 1, 15)
    assert document.total_gross == Decimal("11.90")


async def test_credit_note_artifacts_are_generated_and_uploaded() -> None:
    original = make_original()
    note = make_credit_note(original.id)
    storage = FakeStorage()

    artifacts = await generate_credit_note_documents(
        note,
        original,
        storage,  # type: ignore[arg-type]
        validate_schematron=False,
    )

    assert set(artifacts) == {"zugferd", "xrechnung"}
    assert artifacts["zugferd"]["object_name"].endswith("GS-2099-0001/credit-note.pdf")
    assert artifacts["xrechnung"]["object_name"].endswith("GS-2099-0001/credit-note.xml")
    assert storage.objects[artifacts["zugferd"]["object_name"]][1] == "application/pdf"
    assert storage.objects[artifacts["xrechnung"]["object_name"]][1] == "application/xml"
    assert b"<CreditNote" in storage.objects[artifacts["xrechnung"]["object_name"]][0]
    assert b"381" in storage.objects[artifacts["xrechnung"]["object_name"]][0]
    assert storage.objects[artifacts["zugferd"]["object_name"]][0].startswith(b"%PDF")
