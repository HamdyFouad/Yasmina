"""Invoice billing reads, payment recording and credit-note generation."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import (
    get_credit_note_repository,
    get_invoice_repository,
    get_offer_repository,
    require_roles,
)
from src.api.schemas import InvoiceCreditPayload, InvoicePaymentPayload
from src.api.security import Principal, Role
from src.models.enums import InvoiceStatus
from src.repositories.credit_note_repository import (
    CreditNoteRepository,
    CreditNoteRepositoryError,
)
from src.repositories.invoice_repository import (
    InvoiceRepository,
    InvoiceRepositoryError,
    StoredInvoice,
)
from src.repositories.offer_repository import OfferRepository

__all__ = ["router"]

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _is_sales_only(principal: Principal) -> bool:
    return principal.has_any((Role.SALES,)) and not principal.has_any((Role.ADMIN, Role.VIEWER))


async def _authorize(
    invoice: StoredInvoice,
    principal: Principal,
    offers: OfferRepository,
) -> None:
    if not _is_sales_only(principal):
        return
    offer = await offers.get(invoice.offer_id)
    if offer is None or offer.offer.owner != principal.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sales users may only access their own invoices",
        )


@router.get("")
async def list_invoices(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    invoice_status: InvoiceStatus | None = Query(default=None, alias="status"),
    repository: InvoiceRepository = Depends(get_invoice_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN)),
) -> dict[str, Any]:
    owner = principal.sub if _is_sales_only(principal) else None
    invoices = await repository.list(
        owner=owner,
        status=invoice_status,
        limit=limit,
        offset=offset,
    )
    total = await repository.count(owner=owner, status=invoice_status)
    return {
        "invoices": [invoice.as_dict() for invoice in invoices],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: UUID,
    repository: InvoiceRepository = Depends(get_invoice_repository),
    offers: OfferRepository = Depends(get_offer_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN)),
) -> dict[str, Any]:
    invoice = await repository.get_by_id(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    await _authorize(invoice, principal, offers)
    return {
        "invoice": invoice.as_dict(),
        "events": await repository.events(invoice_id),
        "event_chain_valid": await repository.verify_event_chain(invoice_id),
    }


@router.get("/{invoice_id}/events")
async def list_invoice_events(
    invoice_id: UUID,
    repository: InvoiceRepository = Depends(get_invoice_repository),
    offers: OfferRepository = Depends(get_offer_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN)),
) -> dict[str, Any]:
    invoice = await repository.get_by_id(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    await _authorize(invoice, principal, offers)
    return {
        "invoice_id": str(invoice_id),
        "events": await repository.events(invoice_id),
        "event_chain_valid": await repository.verify_event_chain(invoice_id),
    }


@router.post("/{invoice_id}/payments")
async def record_invoice_payment(
    invoice_id: UUID,
    payload: InvoicePaymentPayload,
    repository: InvoiceRepository = Depends(get_invoice_repository),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    try:
        invoice = await repository.record_payment(
            invoice_id,
            payload.amount,
            actor=principal.sub,
            reference=payload.reference,
        )
    except InvoiceRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return {
        "invoice": invoice.as_dict(),
        "events": await repository.events(invoice_id),
    }


@router.post("/{invoice_id}/credits")
async def generate_invoice_credit(
    invoice_id: UUID,
    payload: InvoiceCreditPayload,
    repository: CreditNoteRepository = Depends(get_credit_note_repository),
    invoices: InvoiceRepository = Depends(get_invoice_repository),
    principal: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    try:
        credit_note = await repository.issue(
            invoice_id,
            amount=payload.amount,
            reason=payload.reason,
            issue_date=payload.issue_date,
            actor=principal.sub,
        )
    except CreditNoteRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    invoice = await invoices.get_by_id(invoice_id)
    return {
        "credit_note": credit_note.as_dict(),
        "invoice": invoice.as_dict() if invoice is not None else None,
        "events": await invoices.events(invoice_id),
    }


@router.get("/{invoice_id}/credit-notes")
async def list_invoice_credit_notes(
    invoice_id: UUID,
    repository: CreditNoteRepository = Depends(get_credit_note_repository),
    invoices: InvoiceRepository = Depends(get_invoice_repository),
    offers: OfferRepository = Depends(get_offer_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN)),
) -> dict[str, Any]:
    invoice = await invoices.get_by_id(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    await _authorize(invoice, principal, offers)
    notes = await repository.list_for_invoice(invoice_id)
    return {
        "invoice_id": str(invoice_id),
        "credit_notes": [note.as_dict() for note in notes],
    }
