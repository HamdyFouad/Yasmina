"""Procurement execution: purchase orders, supplier invoices and margins."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import (
    get_audit_service,
    get_procurement_repository,
    get_supplier_quote_repository,
    require_roles,
)
from src.api.schemas import (
    PurchaseOrderCreatePayload,
    PurchaseOrderStatusPayload,
    SupplierInvoicePayload,
)
from src.api.security import Principal, Role
from src.models.enums import AuditEventType, PurchaseOrderStatus, SupplierInvoiceStatus
from src.repositories.procurement_repository import (
    ProcurementConflict,
    ProcurementNotFound,
    ProcurementRepository,
    ProcurementValidationError,
    SupplierInvoiceInput,
    SupplierInvoiceLineInput,
)
from src.repositories.supplier_quote_repository import SupplierQuoteRepository
from src.services.audit_service import AuditService
from src.services.procurement_service import PurchaseOrderTransitionError

router = APIRouter(prefix="/procurement", tags=["procurement"])

READ_ROLES = (Role.ADMIN, Role.SALES, Role.APPROVER, Role.VIEWER)
WRITE_ROLES = (Role.ADMIN, Role.SALES)


def _procurement_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProcurementNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ProcurementConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=str(exc),
    )


@router.get("/accepted-quotes")
async def list_accepted_quotes(
    repository: SupplierQuoteRepository = Depends(get_supplier_quote_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    """Accepted supplier quotes that do not yet have a purchase order."""

    quotes = await repository.list_accepted_without_order()
    return {
        "quotes": [
            {
                **quote.model_dump(mode="json"),
                "line_count": len(quote.lines),
            }
            for quote in quotes
        ],
        "count": len(quotes),
    }


@router.get("/purchase-orders")
async def list_purchase_orders(
    status_filter: PurchaseOrderStatus | None = Query(default=None, alias="status"),
    supplier_id: UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: ProcurementRepository = Depends(get_procurement_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    rows = await repository.list_purchase_orders(
        status_filter=status_filter,
        supplier_id=supplier_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_purchase_orders(
        status_filter=status_filter,
        supplier_id=supplier_id,
        search=search,
    )
    return {
        "purchase_orders": [row.as_dict() for row in rows],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/purchase-orders", status_code=status.HTTP_201_CREATED)
async def create_purchase_order(
    payload: PurchaseOrderCreatePayload,
    repository: ProcurementRepository = Depends(get_procurement_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, object]:
    try:
        purchase_order = await repository.create_purchase_order_from_quote(
            payload.supplier_quote_id,
            requested_by=principal.sub,
            expected_delivery_date=payload.expected_delivery_date,
            notes=payload.notes,
            customer_offer_id=payload.customer_offer_id,
        )
    except (ProcurementNotFound, ProcurementConflict, ProcurementValidationError) as exc:
        raise _procurement_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.PURCHASE_ORDER_CREATED,
        actor=principal.sub,
        entity_type="purchase_order",
        entity_id=str(purchase_order.id),
        payload={
            "po_number": purchase_order.po_number,
            "supplier_id": str(purchase_order.supplier_id),
            "supplier_quote_id": str(purchase_order.supplier_quote_id),
            "total_net": str(purchase_order.total_net),
            "line_count": len(purchase_order.lines),
        },
    )
    return {"purchase_order": purchase_order.as_dict()}


@router.get("/purchase-orders/{purchase_order_id}")
async def get_purchase_order(
    purchase_order_id: UUID,
    repository: ProcurementRepository = Depends(get_procurement_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    purchase_order = await repository.get_purchase_order(purchase_order_id)
    if purchase_order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="purchase order not found")
    return {"purchase_order": purchase_order.as_dict()}


@router.post("/purchase-orders/{purchase_order_id}/status")
async def transition_purchase_order(
    purchase_order_id: UUID,
    payload: PurchaseOrderStatusPayload,
    repository: ProcurementRepository = Depends(get_procurement_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, object]:
    before = await repository.get_purchase_order(purchase_order_id)
    if before is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="purchase order not found")
    try:
        purchase_order = await repository.transition_purchase_order(
            purchase_order_id,
            payload.status,
        )
    except ProcurementNotFound as exc:
        raise _procurement_error(exc) from exc
    except PurchaseOrderTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await audit.append(
        event_type=AuditEventType.PURCHASE_ORDER_STATUS_CHANGED,
        actor=principal.sub,
        entity_type="purchase_order",
        entity_id=str(purchase_order.id),
        payload={
            "po_number": purchase_order.po_number,
            "from": before.status,
            "to": purchase_order.status,
        },
    )
    return {"purchase_order": purchase_order.as_dict()}


@router.get("/supplier-invoices")
async def list_supplier_invoices(
    status_filter: SupplierInvoiceStatus | None = Query(default=None, alias="status"),
    purchase_order_id: UUID | None = Query(default=None),
    exception_only: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: ProcurementRepository = Depends(get_procurement_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    rows = await repository.list_supplier_invoices(
        status_filter=status_filter,
        purchase_order_id=purchase_order_id,
        exception_only=exception_only,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_supplier_invoices(
        status_filter=status_filter,
        purchase_order_id=purchase_order_id,
        exception_only=exception_only,
        search=search,
    )
    return {
        "supplier_invoices": [row.as_dict() for row in rows],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/supplier-invoices", status_code=status.HTTP_201_CREATED)
async def create_supplier_invoice(
    payload: SupplierInvoicePayload,
    repository: ProcurementRepository = Depends(get_procurement_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, object]:
    data = SupplierInvoiceInput(
        purchase_order_id=payload.purchase_order_id,
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        delivery_date=payload.delivery_date,
        due_date=payload.due_date,
        total_net=payload.total_net,
        vat_rate=payload.vat_rate,
        notes=payload.notes,
        lines=tuple(
            SupplierInvoiceLineInput(
                purchase_order_line_id=line.purchase_order_line_id,
                quantity=line.quantity,
                unit_price_net=line.unit_price_net,
                description=line.description,
            )
            for line in payload.lines
        ),
    )
    try:
        invoice = await repository.create_supplier_invoice(data, received_by=principal.sub)
    except (ProcurementNotFound, ProcurementConflict, ProcurementValidationError) as exc:
        raise _procurement_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SUPPLIER_INVOICE_RECORDED,
        actor=principal.sub,
        entity_type="supplier_invoice",
        entity_id=str(invoice.id),
        payload={
            "invoice_number": invoice.invoice_number,
            "purchase_order_id": str(invoice.purchase_order_id),
            "po_number": invoice.po_number,
            "total_net": str(invoice.total_net),
            "status": invoice.status,
            "exception_codes": list(invoice.exception_codes),
        },
    )
    return {"supplier_invoice": invoice.as_dict()}


@router.get("/supplier-invoices/{invoice_id}")
async def get_supplier_invoice(
    invoice_id: UUID,
    repository: ProcurementRepository = Depends(get_procurement_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    invoice = await repository.get_supplier_invoice(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="supplier invoice not found")
    return {"supplier_invoice": invoice.as_dict()}


@router.post("/supplier-invoices/{invoice_id}/rematch")
async def rematch_supplier_invoice(
    invoice_id: UUID,
    repository: ProcurementRepository = Depends(get_procurement_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, object]:
    try:
        invoice = await repository.rematch_supplier_invoice(invoice_id)
    except ProcurementNotFound as exc:
        raise _procurement_error(exc) from exc
    await audit.append(
        event_type=AuditEventType.SUPPLIER_INVOICE_MATCHED,
        actor=principal.sub,
        entity_type="supplier_invoice",
        entity_id=str(invoice.id),
        payload={
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "exception_codes": list(invoice.exception_codes),
            "variance_net": str(invoice.variance_net),
        },
    )
    return {"supplier_invoice": invoice.as_dict()}


@router.get("/margins")
async def list_margin_summaries(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    repository: ProcurementRepository = Depends(get_procurement_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    summaries = await repository.list_margin_summaries(limit=limit, offset=offset)
    return {
        "margins": [summary.as_dict() for summary in summaries],
        "count": len(summaries),
        "limit": limit,
        "offset": offset,
    }


@router.get("/margins/{offer_id}")
async def get_margin_summary(
    offer_id: UUID,
    repository: ProcurementRepository = Depends(get_procurement_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    summary = await repository.get_margin_for_offer(offer_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="customer offer not found")
    return {"margin": summary.as_dict()}


@router.get("/health")
async def procurement_health(
    repository: ProcurementRepository = Depends(get_procurement_repository),
    _: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    """Lightweight procurement counters for the workspace header."""

    exceptions = await repository.count_supplier_invoices(exception_only=True)
    orders = await repository.count_purchase_orders()
    return {
        "purchase_orders": orders,
        "exceptions": exceptions,
        "today": date.today().isoformat(),
    }
