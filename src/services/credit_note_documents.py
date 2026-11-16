"""Build ZUGFeRD/XRechnung documents for an issued credit note."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.generators.invoice_models import Invoice, InvoiceLine
from src.generators.xrechnung_generator import XRechnungGenerator
from src.generators.zugferd_generator import ZUGFeRDGenerator
from src.integrations.minio_client import MinioObjectStorage, object_name_for_credit_note
from src.models.enums import UnitOfMeasure
from src.repositories.credit_note_repository import StoredCreditNote
from src.repositories.invoice_repository import StoredInvoice

__all__ = ["build_credit_note_invoice", "generate_credit_note_documents"]


def build_credit_note_invoice(
    note: StoredCreditNote,
    original: StoredInvoice,
) -> Invoice:
    """Create the EN16931 document model for a credit note."""

    lines = [
        InvoiceLine(
            line_id=index,
            name=str(line.get("description", "Credit note line"))[:512],
            description=note.reason[:2000] if note.reason else None,
            quantity=Decimal("1"),
            unit=UnitOfMeasure.STUECK,
            unit_price=Decimal(str(line["net_amount"])),
            line_total=Decimal(str(line["net_amount"])),
            vat_rate=Decimal(str(line["vat_rate"])),
        )
        for index, line in enumerate(note.lines, start=1)
    ]
    buyer_reference = original.invoice.buyer_reference or "CREDIT-NOTE"
    return Invoice(
        invoice_number=note.credit_note_number,
        issue_date=note.issue_date,
        due_date=note.issue_date,
        delivery_date=original.invoice.delivery_date,
        currency=original.invoice.currency,
        seller=original.invoice.seller,
        buyer=original.invoice.buyer,
        buyer_reference=buyer_reference,
        order_reference=original.invoice.order_reference,
        lines=lines,
        total_net=note.total_net,
        total_vat=note.total_vat,
        total_gross=note.total_gross,
        payment_terms=f"Credit note for {original.invoice.invoice_number}",
        iban=original.invoice.iban,
        document_type_code="381",
        preceding_invoice_number=original.invoice.invoice_number,
        preceding_invoice_date=original.invoice.issue_date,
    )


async def generate_credit_note_documents(
    note: StoredCreditNote,
    original: StoredInvoice,
    storage: MinioObjectStorage,
    *,
    validate_schematron: bool = True,
) -> dict[str, Any]:
    """Generate, upload and describe the credit-note PDF/XML artifacts."""

    document = build_credit_note_invoice(note, original)
    xml = XRechnungGenerator().build(
        document,
        require_leitweg=original.invoice.buyer_reference is not None,
        validate_schematron=validate_schematron,
    )
    pdf = ZUGFeRDGenerator().build(document)

    xml_key = object_name_for_credit_note(note.credit_note_number, "xml")
    pdf_key = object_name_for_credit_note(note.credit_note_number, "pdf")
    await storage.put_bytes(xml_key, xml, "application/xml")
    await storage.put_bytes(pdf_key, pdf, "application/pdf")
    return {
        "xrechnung": {
            "object_name": xml_key,
            "download_url": (
                f"/api/v1/invoices/{note.invoice_id}/credit-notes/{note.id}/documents/xrechnung"
            ),
            "media_type": "application/xml",
        },
        "zugferd": {
            "object_name": pdf_key,
            "download_url": (f"/api/v1/invoices/{note.invoice_id}/credit-notes/{note.id}/documents/zugferd"),
            "media_type": "application/pdf",
        },
    }
