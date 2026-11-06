"""Bridge CustomerOffer approval to ZUGFeRD/XRechnung artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.api.schemas import InvoiceRequestPayload
from src.config import Settings
from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.generators.xrechnung_generator import XRechnungGenerator
from src.generators.zugferd_generator import ZUGFeRDGenerator
from src.integrations.minio_client import MinioObjectStorage, object_name_for_invoice
from src.models.schemas import CustomerOffer
from src.repositories.invoice_repository import (
    InvoiceRepository,
    InvoiceRepositoryError,
)

__all__ = ["InvoiceWiringError", "build_invoice", "generate_invoice_artifacts"]


class InvoiceWiringError(ValueError):
    """Raised when an approved offer cannot be converted into an e-invoice."""


def _seller(settings: Settings) -> InvoiceParty:
    if not settings.company_street or not settings.company_city or not settings.company_postal_code:
        raise InvoiceWiringError("company street/city/postal code are not configured")
    return InvoiceParty(
        name=settings.company_name,
        street=settings.company_street,
        city=settings.company_city,
        postal_code=settings.company_postal_code,
        country_code=settings.company_country_code,
        vat_id=settings.company_vat_id,
        email=settings.company_email,
        contact_name=settings.company_name,
        telephone=settings.company_phone,
        endpoint_id=settings.company_email,
    )


def build_invoice(
    offer: CustomerOffer,
    settings: Settings,
    request: InvoiceRequestPayload,
) -> Invoice:
    if offer.approved_at is None:
        issue_date = datetime.now(UTC).date()
    else:
        issue_date = offer.approved_at.astimezone(UTC).date()

    lines = [
        InvoiceLine(
            line_id=index,
            name=line.description,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=line.unit_price_net,
            line_total=line.total_net,
            vat_rate=line.vat_rate,
        )
        for index, line in enumerate(offer.lines, start=1)
    ]

    buyer_email = request.buyer.email or offer.customer_contact_email
    buyer = InvoiceParty(
        name=offer.customer_name,
        street=request.buyer.street,
        city=request.buyer.city,
        postal_code=request.buyer.postal_code,
        country_code=request.buyer.country_code,
        vat_id=request.buyer.vat_id,
        email=buyer_email,
        contact_name=offer.customer_name,
        endpoint_id=str(buyer_email) if buyer_email else None,
    )
    return Invoice(
        invoice_number=offer.offer_number,
        issue_date=issue_date,
        due_date=request.due_date,
        delivery_date=request.delivery_date,
        currency=offer.currency,
        seller=_seller(settings),
        buyer=buyer,
        buyer_reference=request.buyer_reference,
        order_reference=offer.customer_reference,
        lines=lines,
        total_net=offer.total_net,
        total_vat=offer.total_vat,
        total_gross=offer.total_gross,
        payment_terms=request.payment_terms,
        iban=request.iban or settings.company_iban,
    )


async def generate_invoice_artifacts(
    offer: CustomerOffer,
    settings: Settings,
    request: InvoiceRequestPayload,
    storage: MinioObjectStorage,
    *,
    invoice_repository: InvoiceRepository | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Generate XML/PDF and persist both in MinIO under the invoice prefix."""

    invoice = build_invoice(offer, settings, request)
    stored_invoice = None
    if invoice_repository is not None:
        try:
            stored_invoice = await invoice_repository.get_by_offer(offer.id)
            if stored_invoice is None:
                stored_invoice = await invoice_repository.create(
                    invoice,
                    offer_id=offer.id,
                    actor=actor,
                )
            invoice = stored_invoice.invoice
        except InvoiceRepositoryError as exc:
            raise InvoiceWiringError(f"invoice persistence failed: {exc}") from exc

    try:
        xrechnung = XRechnungGenerator().build(invoice)
        zugferd = ZUGFeRDGenerator().build(invoice)
    except Exception as exc:
        raise InvoiceWiringError(f"invoice generation failed: {exc}") from exc

    xml_key = object_name_for_invoice(offer.offer_number, "xml")
    pdf_key = object_name_for_invoice(offer.offer_number, "pdf")
    await storage.put_bytes(xml_key, xrechnung, "application/xml")
    await storage.put_bytes(pdf_key, zugferd, "application/pdf")
    artifacts = {
        "xrechnung": {
            "object_name": xml_key,
            "download_url": f"/api/v1/approvals/{offer.offer_number}/documents/xrechnung",
            "media_type": "application/xml",
        },
        "zugferd": {
            "object_name": pdf_key,
            "download_url": f"/api/v1/approvals/{offer.offer_number}/documents/zugferd",
            "media_type": "application/pdf",
        },
    }
    if invoice_repository is not None and stored_invoice is not None:
        try:
            await invoice_repository.attach_artifacts(stored_invoice.id, artifacts)
        except InvoiceRepositoryError as exc:
            raise InvoiceWiringError(f"invoice artifact persistence failed: {exc}") from exc
    return artifacts
