"""Supplier-quote reads and dashboard-created manual quotations."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
    get_audit_service,
    get_session,
    get_supplier_quote_repository,
    get_tender_repository,
    require_roles,
)
from src.api.schemas import ManualSupplierQuotePayload
from src.api.security import Principal, Role
from src.models.enums import AuditEventType, SupplierQuoteStatus
from src.models.schemas import SupplierQuote, SupplierQuoteLine, q2
from src.repositories.supplier_quote_repository import (
    SupplierQuoteRepository,
    SupplierQuoteRepositoryError,
)
from src.repositories.tender_repository import StoredTender, TenderRepository
from src.services.audit_service import AuditService, AuditServiceError

__all__ = ["router"]

logger = logging.getLogger("yasmina.api.supplier_quotes")

router = APIRouter(prefix="/supplier-quotes", tags=["supplier-quotes"])


def _is_sales_only(principal: Principal) -> bool:
    return principal.has_any((Role.SALES,)) and not principal.has_any(
        (Role.ADMIN, Role.VIEWER, Role.APPROVER)
    )


def _ensure_tender_access(tender: StoredTender, principal: Principal) -> None:
    if _is_sales_only(principal) and tender.tender.owner != principal.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sales users may only read quotes for their own tenders",
        )


def _quote_payload(quote: SupplierQuote) -> dict[str, Any]:
    manual = str(quote.raw_extraction.get("source", "")).lower() in {"dashboard", "manual"}
    return {
        **quote.model_dump(mode="json"),
        "source": "DASHBOARD" if manual else "N8N",
        "line_count": len(quote.lines),
    }


@router.get("")
async def list_supplier_quotes(
    tender_id: UUID = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: SupplierQuoteRepository = Depends(get_supplier_quote_repository),
    tenders: TenderRepository = Depends(get_tender_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """List supplier quotes for a tender with their comparison fields."""

    stored = await tenders.get(tender_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tender not found")
    _ensure_tender_access(stored, principal)
    quotes = await repository.list_for_tender(tender_id, limit=limit, offset=offset)
    total = await repository.count_for_tender(tender_id)
    return {
        "quotes": [_quote_payload(quote) for quote in quotes],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{quote_id}")
async def get_supplier_quote(
    quote_id: UUID,
    repository: SupplierQuoteRepository = Depends(get_supplier_quote_repository),
    tenders: TenderRepository = Depends(get_tender_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """Read one supplier quote, including its priced lines."""

    quote = await repository.get(quote_id)
    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="supplier quote not found")
    if _is_sales_only(principal):
        if quote.tender_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="sales users may only read quotes for their own tenders",
            )
        stored = await tenders.get(quote.tender_id)
        if stored is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tender not found")
        _ensure_tender_access(stored, principal)
    return {"quote": _quote_payload(quote)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_supplier_quote(
    payload: ManualSupplierQuotePayload,
    repository: SupplierQuoteRepository = Depends(get_supplier_quote_repository),
    tenders: TenderRepository = Depends(get_tender_repository),
    audit: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_roles(Role.SALES, Role.ADMIN)),
) -> dict[str, Any]:
    """Create a dashboard-entered supplier quote for a tender."""

    stored = await tenders.get(payload.tender_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tender not found")
    _ensure_tender_access(stored, principal)

    tender_item_ids = {item.id for item in stored.tender.items}
    unknown = sorted(
        str(line.tender_item_id) for line in payload.lines if line.tender_item_id not in tender_item_ids
    )
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="quote line references unknown tender item(s): " + ", ".join(unknown),
        )

    quote_id = uuid4()
    lines = [
        SupplierQuoteLine(
            supplier_quote_id=quote_id,
            line_number=line.line_number,
            description=line.description,
            tender_item_id=line.tender_item_id,
            quantity=line.quantity,
            unit=line.unit,
            unit_price_net=line.unit_price_net,
            moq=line.moq,
            pack_size=line.pack_size,
            delivery_days=line.delivery_days,
            notes=line.notes,
        )
        for line in payload.lines
    ]
    quote = SupplierQuote(
        id=quote_id,
        supplier_name=payload.supplier_name,
        supplier_contact_email=str(payload.supplier_contact_email)
        if payload.supplier_contact_email
        else None,
        quote_reference=payload.quote_reference,
        tender_id=payload.tender_id,
        status=SupplierQuoteStatus.RECEIVED,
        currency=stored.tender.currency,
        valid_until=payload.valid_until,
        payment_terms=payload.payment_terms,
        incoterms=payload.incoterms,
        delivery_days=payload.delivery_days,
        lines=lines,
        total_net=q2(
            sum(
                (line.quantity * line.unit_price_net for line in lines),
                start=Decimal("0.00"),
            )
        ),
        raw_extraction={"source": "dashboard"},
    )

    try:
        stored_quote = await repository.save(quote)
        await session.commit()
    except SupplierQuoteRepositoryError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if not stored_quote.created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a quote with this supplier and reference already exists",
        )

    try:
        await audit.append(
            event_type=AuditEventType.SUPPLIER_QUOTE_INGESTED,
            actor=principal.sub,
            entity_type="supplier_quote",
            entity_id=str(stored_quote.quote_id),
            payload={
                "source": "dashboard",
                "tender_id": str(payload.tender_id),
                "quote_reference": quote.quote_reference,
                "line_count": len(lines),
            },
        )
    except AuditServiceError as exc:
        logger.warning("manual supplier quote %s saved but audit append failed: %s", quote_id, exc)

    return {"quote": _quote_payload(quote)}
