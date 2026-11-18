"""Persistence and query services for purchase orders and supplier invoices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.enums import (
    CustomerOfferStatus,
    PurchaseOrderStatus,
    SupplierInvoiceStatus,
    SupplierQuoteStatus,
)
from src.models.orm import CustomerOffer as CustomerOfferORM
from src.models.orm import PurchaseOrder as PurchaseOrderORM
from src.models.orm import PurchaseOrderLine as PurchaseOrderLineORM
from src.models.orm import SupplierInvoice as SupplierInvoiceORM
from src.models.orm import SupplierInvoiceLine as SupplierInvoiceLineORM
from src.models.orm import SupplierQuote as SupplierQuoteORM
from src.models.schemas import q2
from src.services.procurement_service import (
    MarginInvoice,
    MarginOffer,
    MarginOfferLine,
    MarginSummary,
    MatchInvoiceLine,
    MatchPoLine,
    MatchPurchaseOrder,
    MatchSupplierInvoice,
    calculate_margin,
    ensure_purchase_order_transition,
    match_supplier_invoice,
)

__all__ = [
    "ProcurementConflict",
    "ProcurementNotFound",
    "ProcurementRepository",
    "ProcurementValidationError",
    "PurchaseOrderLineView",
    "PurchaseOrderView",
    "SupplierInvoiceInput",
    "SupplierInvoiceLineInput",
    "SupplierInvoiceLineView",
    "SupplierInvoiceView",
]

MONEY_TOLERANCE = Decimal("0.01")
APPROVED_OFFER_STATES = {
    CustomerOfferStatus.APPROVED,
    CustomerOfferStatus.SENT,
    CustomerOfferStatus.ACCEPTED,
}


class ProcurementNotFound(ValueError):
    """Raised when a procurement entity does not exist."""


class ProcurementConflict(ValueError):
    """Raised when a unique procurement invariant is violated."""


class ProcurementValidationError(ValueError):
    """Raised when a procurement payload cannot be applied."""


@dataclass(frozen=True, slots=True)
class PurchaseOrderLineView:
    id: UUID
    purchase_order_id: UUID
    supplier_quote_line_id: UUID
    line_number: int
    description: str
    tender_item_id: UUID | None
    quantity: Decimal
    unit: str
    unit_price_net: Decimal
    total_net: Decimal
    received_quantity: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "purchase_order_id": str(self.purchase_order_id),
            "supplier_quote_line_id": str(self.supplier_quote_line_id),
            "line_number": self.line_number,
            "description": self.description,
            "tender_item_id": str(self.tender_item_id) if self.tender_item_id else None,
            "quantity": str(self.quantity),
            "unit": self.unit,
            "unit_price_net": str(self.unit_price_net),
            "total_net": str(self.total_net),
            "received_quantity": str(self.received_quantity),
        }


@dataclass(frozen=True, slots=True)
class PurchaseOrderView:
    id: UUID
    po_number: str
    supplier_id: UUID
    supplier_name: str
    supplier_contact_email: str | None
    tender_id: UUID | None
    supplier_quote_id: UUID | None
    customer_offer_id: UUID | None
    status: str
    currency: str
    order_date: date
    expected_delivery_date: date | None
    sent_at: datetime | None
    confirmed_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    total_net: Decimal
    notes: str | None
    requested_by: str
    lines: tuple[PurchaseOrderLineView, ...]
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "po_number": self.po_number,
            "supplier_id": str(self.supplier_id),
            "supplier_name": self.supplier_name,
            "supplier_contact_email": self.supplier_contact_email,
            "tender_id": str(self.tender_id) if self.tender_id else None,
            "supplier_quote_id": str(self.supplier_quote_id) if self.supplier_quote_id else None,
            "customer_offer_id": (str(self.customer_offer_id) if self.customer_offer_id else None),
            "status": self.status,
            "currency": self.currency,
            "order_date": self.order_date.isoformat(),
            "expected_delivery_date": (
                self.expected_delivery_date.isoformat() if self.expected_delivery_date is not None else None
            ),
            "sent_at": self.sent_at.isoformat() if self.sent_at is not None else None,
            "confirmed_at": (self.confirmed_at.isoformat() if self.confirmed_at is not None else None),
            "delivered_at": (self.delivered_at.isoformat() if self.delivered_at is not None else None),
            "cancelled_at": (self.cancelled_at.isoformat() if self.cancelled_at is not None else None),
            "total_net": str(self.total_net),
            "notes": self.notes,
            "requested_by": self.requested_by,
            "lines": [line.as_dict() for line in self.lines],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SupplierInvoiceLineInput:
    purchase_order_line_id: UUID
    quantity: Decimal
    unit_price_net: Decimal
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SupplierInvoiceInput:
    purchase_order_id: UUID
    invoice_number: str
    invoice_date: date
    total_net: Decimal
    delivery_date: date | None = None
    due_date: date | None = None
    vat_rate: Decimal = Decimal("0.1900")
    notes: str | None = None
    lines: tuple[SupplierInvoiceLineInput, ...] = ()


@dataclass(frozen=True, slots=True)
class SupplierInvoiceLineView:
    id: UUID
    supplier_invoice_id: UUID
    purchase_order_line_id: UUID
    description: str
    quantity: Decimal
    unit: str
    unit_price_net: Decimal
    total_net: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "supplier_invoice_id": str(self.supplier_invoice_id),
            "purchase_order_line_id": str(self.purchase_order_line_id),
            "description": self.description,
            "quantity": str(self.quantity),
            "unit": self.unit,
            "unit_price_net": str(self.unit_price_net),
            "total_net": str(self.total_net),
        }


@dataclass(frozen=True, slots=True)
class SupplierInvoiceView:
    id: UUID
    purchase_order_id: UUID
    po_number: str
    supplier_id: UUID
    supplier_name: str
    invoice_number: str
    invoice_date: date
    delivery_date: date | None
    due_date: date | None
    currency: str
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    status: str
    exception_codes: tuple[str, ...]
    variance_net: Decimal
    match_details: dict[str, Any]
    matched_at: datetime | None
    notes: str | None
    received_by: str
    lines: tuple[SupplierInvoiceLineView, ...]
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "purchase_order_id": str(self.purchase_order_id),
            "po_number": self.po_number,
            "supplier_id": str(self.supplier_id),
            "supplier_name": self.supplier_name,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date.isoformat(),
            "delivery_date": (self.delivery_date.isoformat() if self.delivery_date is not None else None),
            "due_date": self.due_date.isoformat() if self.due_date is not None else None,
            "currency": self.currency,
            "total_net": str(self.total_net),
            "total_vat": str(self.total_vat),
            "total_gross": str(self.total_gross),
            "status": self.status,
            "exception_codes": list(self.exception_codes),
            "variance_net": str(self.variance_net),
            "match_details": self.match_details,
            "matched_at": (self.matched_at.isoformat() if self.matched_at is not None else None),
            "notes": self.notes,
            "received_by": self.received_by,
            "lines": [line.as_dict() for line in self.lines],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _po_line_view(row: PurchaseOrderLineORM) -> PurchaseOrderLineView:
    return PurchaseOrderLineView(
        id=row.id,
        purchase_order_id=row.purchase_order_id,
        supplier_quote_line_id=row.supplier_quote_line_id,
        line_number=row.line_number,
        description=row.description,
        tender_item_id=row.tender_item_id,
        quantity=row.quantity,
        unit=row.unit.value if hasattr(row.unit, "value") else str(row.unit),
        unit_price_net=row.unit_price_net,
        total_net=row.total_net,
        received_quantity=row.received_quantity,
    )


def _po_view(row: PurchaseOrderORM) -> PurchaseOrderView:
    return PurchaseOrderView(
        id=row.id,
        po_number=row.po_number,
        supplier_id=row.supplier_id,
        supplier_name=row.supplier_name,
        supplier_contact_email=row.supplier_contact_email,
        tender_id=row.tender_id,
        supplier_quote_id=row.supplier_quote_id,
        customer_offer_id=row.customer_offer_id,
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        currency=row.currency.value if hasattr(row.currency, "value") else str(row.currency),
        order_date=row.order_date,
        expected_delivery_date=row.expected_delivery_date,
        sent_at=row.sent_at,
        confirmed_at=row.confirmed_at,
        delivered_at=row.delivered_at,
        cancelled_at=row.cancelled_at,
        total_net=row.total_net,
        notes=row.notes,
        requested_by=row.requested_by,
        lines=tuple(_po_line_view(line) for line in row.lines),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _invoice_line_view(row: SupplierInvoiceLineORM) -> SupplierInvoiceLineView:
    return SupplierInvoiceLineView(
        id=row.id,
        supplier_invoice_id=row.supplier_invoice_id,
        purchase_order_line_id=row.purchase_order_line_id,
        description=row.description,
        quantity=row.quantity,
        unit=row.unit.value if hasattr(row.unit, "value") else str(row.unit),
        unit_price_net=row.unit_price_net,
        total_net=row.total_net,
    )


def _invoice_view(row: SupplierInvoiceORM) -> SupplierInvoiceView:
    return SupplierInvoiceView(
        id=row.id,
        purchase_order_id=row.purchase_order_id,
        po_number=row.po_number,
        supplier_id=row.supplier_id,
        supplier_name=row.supplier_name,
        invoice_number=row.invoice_number,
        invoice_date=row.invoice_date,
        delivery_date=row.delivery_date,
        due_date=row.due_date,
        currency=row.currency.value if hasattr(row.currency, "value") else str(row.currency),
        total_net=row.total_net,
        total_vat=row.total_vat,
        total_gross=row.total_gross,
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        exception_codes=tuple(row.exception_codes or ()),
        variance_net=row.variance_net,
        match_details=dict(row.match_details or {}),
        matched_at=row.matched_at,
        notes=row.notes,
        received_by=row.received_by,
        lines=tuple(_invoice_line_view(line) for line in row.lines),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _match_po(row: PurchaseOrderORM) -> MatchPurchaseOrder:
    return MatchPurchaseOrder(
        id=row.id,
        total_net=row.total_net,
        expected_delivery_date=row.expected_delivery_date,
        lines=tuple(
            MatchPoLine(
                id=line.id,
                line_number=line.line_number,
                description=line.description,
                quantity=line.quantity,
                unit_price_net=line.unit_price_net,
                total_net=line.total_net,
            )
            for line in row.lines
        ),
    )


def _match_invoice(row: SupplierInvoiceORM) -> MatchSupplierInvoice:
    return MatchSupplierInvoice(
        id=row.id,
        total_net=row.total_net,
        delivery_date=row.delivery_date,
        lines=tuple(
            MatchInvoiceLine(
                id=line.id,
                purchase_order_line_id=line.purchase_order_line_id,
                description=line.description,
                quantity=line.quantity,
                unit_price_net=line.unit_price_net,
                total_net=line.total_net,
            )
            for line in row.lines
        ),
    )


def _margin_offer(row: CustomerOfferORM) -> MarginOffer:
    return MarginOffer(
        id=row.id,
        offer_number=row.offer_number,
        customer_name=row.customer_name,
        currency=row.currency.value if hasattr(row.currency, "value") else str(row.currency),
        total_net=row.total_net,
        lines=tuple(
            MarginOfferLine(
                quantity=line.quantity,
                cost_unit_price_net=line.cost_unit_price_net,
            )
            for line in row.lines
        ),
    )


class ProcurementRepository:
    """Purchase-order lifecycle, supplier-invoice matching and margin reads."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- purchase orders -------------------------------------------------

    def _po_statement(self) -> Select[PurchaseOrderORM]:
        return select(PurchaseOrderORM).options(selectinload(PurchaseOrderORM.lines))

    async def list_purchase_orders(
        self,
        *,
        status_filter: str | None = None,
        supplier_id: UUID | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PurchaseOrderView]:
        statement = (
            self._po_statement().order_by(PurchaseOrderORM.created_at.desc()).offset(offset).limit(limit)
        )
        if status_filter is not None:
            statement = statement.where(PurchaseOrderORM.status == status_filter)
        if supplier_id is not None:
            statement = statement.where(PurchaseOrderORM.supplier_id == supplier_id)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    PurchaseOrderORM.po_number.ilike(pattern),
                    PurchaseOrderORM.supplier_name.ilike(pattern),
                )
            )
        rows = (await self.session.execute(statement)).scalars().all()
        return [_po_view(row) for row in rows]

    async def count_purchase_orders(
        self,
        *,
        status_filter: str | None = None,
        supplier_id: UUID | None = None,
        search: str | None = None,
    ) -> int:
        statement = select(func.count()).select_from(PurchaseOrderORM)
        if status_filter is not None:
            statement = statement.where(PurchaseOrderORM.status == status_filter)
        if supplier_id is not None:
            statement = statement.where(PurchaseOrderORM.supplier_id == supplier_id)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    PurchaseOrderORM.po_number.ilike(pattern),
                    PurchaseOrderORM.supplier_name.ilike(pattern),
                )
            )
        return int((await self.session.execute(statement)).scalar_one())

    async def get_purchase_order(self, purchase_order_id: UUID) -> PurchaseOrderView | None:
        row = (
            await self.session.execute(self._po_statement().where(PurchaseOrderORM.id == purchase_order_id))
        ).scalar_one_or_none()
        return _po_view(row) if row is not None else None

    async def create_purchase_order_from_quote(
        self,
        quote_id: UUID,
        *,
        requested_by: str,
        expected_delivery_date: date | None = None,
        notes: str | None = None,
        customer_offer_id: UUID | None = None,
    ) -> PurchaseOrderView:
        quote = (
            await self.session.execute(
                select(SupplierQuoteORM)
                .where(SupplierQuoteORM.id == quote_id)
                .options(selectinload(SupplierQuoteORM.lines))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if quote is None:
            raise ProcurementNotFound("supplier quote not found")
        if quote.status != SupplierQuoteStatus.ACCEPTED:
            raise ProcurementValidationError("supplier quote must be accepted before creating a PO")
        if quote.supplier_id is None:
            raise ProcurementValidationError("supplier quote has no linked supplier master data")
        accepted_lines = [line for line in quote.lines if line.is_accepted]
        if not accepted_lines:
            raise ProcurementValidationError("supplier quote has no accepted lines")

        existing = (
            await self.session.execute(
                select(PurchaseOrderORM.id).where(PurchaseOrderORM.supplier_quote_id == quote.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ProcurementConflict("a purchase order already exists for this supplier quote")

        offer = await self._resolve_customer_offer(quote.tender_id, customer_offer_id)
        order_date = date.today()
        delivery_days = quote.delivery_days if quote.delivery_days is not None else 14
        expected = expected_delivery_date or (order_date + timedelta(days=delivery_days))
        if expected < order_date:
            raise ProcurementValidationError("expected delivery date must not be in the past")
        total_net = q2(
            sum(
                (q2(line.quantity * line.unit_price_net) for line in accepted_lines),
                start=Decimal("0.00"),
            )
        )
        row = PurchaseOrderORM(
            po_number=f"PO-{order_date.year}-{uuid4().hex[:8].upper()}",
            supplier_id=quote.supplier_id,
            supplier_name=quote.supplier_name,
            supplier_contact_email=quote.supplier_contact_email,
            tender_id=quote.tender_id,
            supplier_quote_id=quote.id,
            customer_offer_id=offer.id if offer is not None else None,
            status=PurchaseOrderStatus.DRAFT,
            currency=quote.currency,
            order_date=order_date,
            expected_delivery_date=expected,
            total_net=total_net,
            notes=notes,
            requested_by=requested_by,
            lines=[
                PurchaseOrderLineORM(
                    supplier_quote_line_id=line.id,
                    line_number=line.line_number,
                    description=line.description,
                    tender_item_id=line.tender_item_id,
                    quantity=line.quantity,
                    unit=line.unit,
                    unit_price_net=line.unit_price_net,
                    total_net=q2(line.quantity * line.unit_price_net),
                )
                for line in accepted_lines
            ],
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ProcurementConflict("purchase order conflicts with existing data") from exc
        await self.session.refresh(row, attribute_names=["created_at", "updated_at"])
        await self.session.refresh(row, attribute_names=["lines"])
        return _po_view(row)

    async def _resolve_customer_offer(
        self,
        tender_id: UUID | None,
        customer_offer_id: UUID | None,
    ) -> CustomerOfferORM | None:
        if customer_offer_id is not None:
            offer = await self.session.get(CustomerOfferORM, customer_offer_id)
            if offer is None:
                raise ProcurementNotFound("customer offer not found")
            if offer.status not in APPROVED_OFFER_STATES:
                raise ProcurementValidationError(
                    "customer offer must be approved before it can be linked to a PO"
                )
            if tender_id is not None and offer.tender_id not in {None, tender_id}:
                raise ProcurementValidationError("customer offer belongs to a different tender")
            return offer
        if tender_id is None:
            return None
        return (
            await self.session.execute(
                select(CustomerOfferORM)
                .where(
                    CustomerOfferORM.tender_id == tender_id,
                    CustomerOfferORM.status.in_(tuple(APPROVED_OFFER_STATES)),
                )
                .order_by(CustomerOfferORM.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def transition_purchase_order(
        self,
        purchase_order_id: UUID,
        target: PurchaseOrderStatus,
    ) -> PurchaseOrderView:
        row = (
            await self.session.execute(
                self._po_statement().where(PurchaseOrderORM.id == purchase_order_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ProcurementNotFound("purchase order not found")
        ensure_purchase_order_transition(row.status, target)
        if row.status == target:
            return _po_view(row)
        now = datetime.now(UTC)
        row.status = target
        if target == PurchaseOrderStatus.SENT:
            row.sent_at = now
        elif target == PurchaseOrderStatus.CONFIRMED:
            row.confirmed_at = now
        elif target == PurchaseOrderStatus.DELIVERED:
            row.delivered_at = now
            for line in row.lines:
                line.received_quantity = line.quantity
        elif target == PurchaseOrderStatus.CANCELLED:
            row.cancelled_at = now
        await self.session.commit()
        await self.session.refresh(row, attribute_names=["updated_at"])
        await self.session.refresh(row, attribute_names=["lines"])
        return _po_view(row)

    # -- supplier invoices ----------------------------------------------

    def _invoice_statement(self) -> Select[SupplierInvoiceORM]:
        return select(SupplierInvoiceORM).options(selectinload(SupplierInvoiceORM.lines))

    async def list_supplier_invoices(
        self,
        *,
        status_filter: str | None = None,
        purchase_order_id: UUID | None = None,
        exception_only: bool = False,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SupplierInvoiceView]:
        statement = (
            select(SupplierInvoiceORM)
            .options(selectinload(SupplierInvoiceORM.lines))
            .order_by(SupplierInvoiceORM.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if exception_only:
            statement = statement.where(SupplierInvoiceORM.status == SupplierInvoiceStatus.EXCEPTION)
        elif status_filter is not None:
            statement = statement.where(SupplierInvoiceORM.status == status_filter)
        if purchase_order_id is not None:
            statement = statement.where(SupplierInvoiceORM.purchase_order_id == purchase_order_id)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    SupplierInvoiceORM.invoice_number.ilike(pattern),
                    SupplierInvoiceORM.supplier_name.ilike(pattern),
                )
            )
        rows = (await self.session.execute(statement)).scalars().all()
        return [_invoice_view(row) for row in rows]

    async def count_supplier_invoices(
        self,
        *,
        status_filter: str | None = None,
        purchase_order_id: UUID | None = None,
        exception_only: bool = False,
        search: str | None = None,
    ) -> int:
        statement = select(func.count()).select_from(SupplierInvoiceORM)
        if exception_only:
            statement = statement.where(SupplierInvoiceORM.status == SupplierInvoiceStatus.EXCEPTION)
        elif status_filter is not None:
            statement = statement.where(SupplierInvoiceORM.status == status_filter)
        if purchase_order_id is not None:
            statement = statement.where(SupplierInvoiceORM.purchase_order_id == purchase_order_id)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    SupplierInvoiceORM.invoice_number.ilike(pattern),
                    SupplierInvoiceORM.supplier_name.ilike(pattern),
                )
            )
        return int((await self.session.execute(statement)).scalar_one())

    async def get_supplier_invoice(self, invoice_id: UUID) -> SupplierInvoiceView | None:
        row = (
            await self.session.execute(self._invoice_statement().where(SupplierInvoiceORM.id == invoice_id))
        ).scalar_one_or_none()
        return _invoice_view(row) if row is not None else None

    async def create_supplier_invoice(
        self,
        payload: SupplierInvoiceInput,
        *,
        received_by: str,
    ) -> SupplierInvoiceView:
        purchase_order = (
            await self.session.execute(
                self._po_statement().where(PurchaseOrderORM.id == payload.purchase_order_id).with_for_update()
            )
        ).scalar_one_or_none()
        if purchase_order is None:
            raise ProcurementNotFound("purchase order not found")

        po_lines = {line.id: line for line in purchase_order.lines}
        input_lines: list[SupplierInvoiceLineInput] = list(payload.lines)
        invoice_line_rows: list[SupplierInvoiceLineORM] = []
        seen: set[UUID] = set()
        for line in input_lines:
            po_line = po_lines.get(line.purchase_order_line_id)
            if po_line is None:
                raise ProcurementValidationError(
                    "supplier invoice line references an unknown purchase-order line"
                )
            if line.purchase_order_line_id in seen:
                raise ProcurementValidationError("supplier invoice repeats a purchase-order line")
            seen.add(line.purchase_order_line_id)
            invoice_line_rows.append(
                SupplierInvoiceLineORM(
                    purchase_order_line_id=po_line.id,
                    description=line.description or po_line.description,
                    quantity=line.quantity,
                    unit=po_line.unit,
                    unit_price_net=line.unit_price_net,
                    total_net=q2(line.quantity * line.unit_price_net),
                )
            )

        if invoice_line_rows:
            lines_total = q2(sum((line.total_net for line in invoice_line_rows), Decimal("0.00")))
            if abs(lines_total - payload.total_net) > MONEY_TOLERANCE:
                raise ProcurementValidationError("supplier invoice total does not equal its line totals")

        total_net = q2(payload.total_net)
        total_vat = q2(total_net * payload.vat_rate)
        total_gross = q2(total_net + total_vat)
        now = datetime.now(UTC)
        provisional = MatchSupplierInvoice(
            id=uuid4(),
            total_net=total_net,
            delivery_date=payload.delivery_date,
            lines=tuple(
                MatchInvoiceLine(
                    id=uuid4(),
                    purchase_order_line_id=line.purchase_order_line_id,
                    description=line.description or "",
                    quantity=line.quantity,
                    unit_price_net=line.unit_price_net,
                    total_net=q2(line.quantity * line.unit_price_net),
                )
                for line in input_lines
            ),
        )
        result = match_supplier_invoice(_match_po(purchase_order), provisional, matched_at=now)
        row = SupplierInvoiceORM(
            purchase_order_id=purchase_order.id,
            supplier_id=purchase_order.supplier_id,
            supplier_name=purchase_order.supplier_name,
            po_number=purchase_order.po_number,
            invoice_number=payload.invoice_number.strip(),
            invoice_date=payload.invoice_date,
            delivery_date=payload.delivery_date,
            due_date=payload.due_date,
            currency=purchase_order.currency,
            total_net=total_net,
            vat_rate=payload.vat_rate,
            total_vat=total_vat,
            total_gross=total_gross,
            status=result.status,
            exception_codes=list(result.exception_codes),
            match_details=result.as_dict(),
            variance_net=result.variance_net,
            matched_at=now,
            notes=payload.notes,
            received_by=received_by,
            lines=invoice_line_rows,
        )
        self.session.add(row)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ProcurementConflict(
                "a supplier invoice with this supplier and invoice number already exists"
            ) from exc
        await self.session.refresh(row, attribute_names=["created_at", "updated_at"])
        await self.session.refresh(row, attribute_names=["lines"])
        return _invoice_view(row)

    async def rematch_supplier_invoice(self, invoice_id: UUID) -> SupplierInvoiceView:
        invoice = (
            await self.session.execute(
                self._invoice_statement().where(SupplierInvoiceORM.id == invoice_id).with_for_update()
            )
        ).scalar_one_or_none()
        if invoice is None:
            raise ProcurementNotFound("supplier invoice not found")
        purchase_order = (
            await self.session.execute(
                self._po_statement().where(PurchaseOrderORM.id == invoice.purchase_order_id)
            )
        ).scalar_one_or_none()
        if purchase_order is None:
            raise ProcurementNotFound("purchase order not found")
        now = datetime.now(UTC)
        result = match_supplier_invoice(_match_po(purchase_order), _match_invoice(invoice), matched_at=now)
        invoice.status = result.status
        invoice.exception_codes = list(result.exception_codes)
        invoice.match_details = result.as_dict()
        invoice.variance_net = result.variance_net
        invoice.matched_at = now
        await self.session.commit()
        await self.session.refresh(invoice, attribute_names=["updated_at"])
        return _invoice_view(invoice)

    # -- margin ----------------------------------------------------------

    async def list_margin_summaries(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MarginSummary]:
        offers = (
            (
                await self.session.execute(
                    select(CustomerOfferORM)
                    .join(
                        PurchaseOrderORM,
                        PurchaseOrderORM.customer_offer_id == CustomerOfferORM.id,
                    )
                    .distinct()
                    .options(selectinload(CustomerOfferORM.lines))
                    .order_by(CustomerOfferORM.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [await self._margin_for_offer(offer) for offer in offers]

    async def get_margin_for_offer(self, offer_id: UUID) -> MarginSummary | None:
        offer = (
            await self.session.execute(
                select(CustomerOfferORM)
                .where(CustomerOfferORM.id == offer_id)
                .options(selectinload(CustomerOfferORM.lines))
            )
        ).scalar_one_or_none()
        if offer is None:
            return None
        return await self._margin_for_offer(offer)

    async def _margin_for_offer(self, offer: CustomerOfferORM) -> MarginSummary:
        invoices = (
            (
                await self.session.execute(
                    select(SupplierInvoiceORM)
                    .join(
                        PurchaseOrderORM,
                        SupplierInvoiceORM.purchase_order_id == PurchaseOrderORM.id,
                    )
                    .where(PurchaseOrderORM.customer_offer_id == offer.id)
                )
            )
            .scalars()
            .all()
        )
        purchase_order_count = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(PurchaseOrderORM)
                    .where(PurchaseOrderORM.customer_offer_id == offer.id)
                )
            ).scalar_one()
        )
        return calculate_margin(
            _margin_offer(offer),
            tuple(
                MarginInvoice(
                    total_net=invoice.total_net,
                    status=invoice.status,
                    exception_codes=tuple(invoice.exception_codes or ()),
                )
                for invoice in invoices
            ),
            purchase_order_count=purchase_order_count,
        )
