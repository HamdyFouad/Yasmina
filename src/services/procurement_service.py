"""Deterministic procurement matching, margin and purchase-order state rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.models.enums import PurchaseOrderStatus, SupplierInvoiceStatus
from src.models.schemas import q2, q4

__all__ = [
    "ALLOWED_PURCHASE_ORDER_TRANSITIONS",
    "InvoiceMatchLine",
    "InvoiceMatchResult",
    "MarginInvoice",
    "MarginOffer",
    "MarginOfferLine",
    "MarginSummary",
    "ProcurementMatchError",
    "PurchaseOrderTransitionError",
    "calculate_margin",
    "ensure_purchase_order_transition",
    "match_supplier_invoice",
]

MONEY_TOLERANCE = Decimal("0.01")
QUANTITY_TOLERANCE = Decimal("0.0001")
PRICE_TOLERANCE = Decimal("0.0001")

ALLOWED_PURCHASE_ORDER_TRANSITIONS: dict[PurchaseOrderStatus, frozenset[PurchaseOrderStatus]] = {
    PurchaseOrderStatus.DRAFT: frozenset({PurchaseOrderStatus.SENT, PurchaseOrderStatus.CANCELLED}),
    PurchaseOrderStatus.SENT: frozenset({PurchaseOrderStatus.CONFIRMED, PurchaseOrderStatus.CANCELLED}),
    PurchaseOrderStatus.CONFIRMED: frozenset({PurchaseOrderStatus.DELIVERED, PurchaseOrderStatus.CANCELLED}),
    PurchaseOrderStatus.DELIVERED: frozenset(),
    PurchaseOrderStatus.CANCELLED: frozenset(),
}


class PurchaseOrderTransitionError(ValueError):
    """Raised when a purchase-order status transition is not allowed."""


class ProcurementMatchError(ValueError):
    """Raised when supplier-invoice matching input is inconsistent."""


def ensure_purchase_order_transition(
    current: PurchaseOrderStatus,
    target: PurchaseOrderStatus,
) -> None:
    """Validate one purchase-order transition; same-state calls are idempotent."""

    if current == target:
        return
    if target not in ALLOWED_PURCHASE_ORDER_TRANSITIONS[current]:
        allowed = ", ".join(status.value for status in sorted(ALLOWED_PURCHASE_ORDER_TRANSITIONS[current]))
        raise PurchaseOrderTransitionError(
            f"cannot transition purchase order from {current.value} to {target.value}; "
            f"allowed: {allowed or 'none'}"
        )


@dataclass(frozen=True, slots=True)
class MatchPoLine:
    id: UUID
    line_number: int
    description: str
    quantity: Decimal
    unit_price_net: Decimal
    total_net: Decimal


@dataclass(frozen=True, slots=True)
class MatchInvoiceLine:
    id: UUID
    purchase_order_line_id: UUID
    description: str
    quantity: Decimal
    unit_price_net: Decimal
    total_net: Decimal


@dataclass(frozen=True, slots=True)
class MatchPurchaseOrder:
    id: UUID
    total_net: Decimal
    expected_delivery_date: date | None
    lines: tuple[MatchPoLine, ...]


@dataclass(frozen=True, slots=True)
class MatchSupplierInvoice:
    id: UUID
    total_net: Decimal
    delivery_date: date | None
    lines: tuple[MatchInvoiceLine, ...]


@dataclass(frozen=True, slots=True)
class InvoiceMatchLine:
    purchase_order_line_id: UUID
    line_number: int
    description: str
    expected_quantity: Decimal
    actual_quantity: Decimal
    expected_unit_price_net: Decimal
    actual_unit_price_net: Decimal
    expected_total_net: Decimal
    actual_total_net: Decimal
    quantity_variance: Decimal
    price_variance: Decimal
    total_variance: Decimal
    exception_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "purchase_order_line_id": str(self.purchase_order_line_id),
            "line_number": self.line_number,
            "description": self.description,
            "expected_quantity": str(self.expected_quantity),
            "actual_quantity": str(self.actual_quantity),
            "expected_unit_price_net": str(self.expected_unit_price_net),
            "actual_unit_price_net": str(self.actual_unit_price_net),
            "expected_total_net": str(self.expected_total_net),
            "actual_total_net": str(self.actual_total_net),
            "quantity_variance": str(self.quantity_variance),
            "price_variance": str(self.price_variance),
            "total_variance": str(self.total_variance),
            "exception_codes": list(self.exception_codes),
        }


@dataclass(frozen=True, slots=True)
class InvoiceMatchResult:
    status: SupplierInvoiceStatus
    exception_codes: tuple[str, ...]
    variance_net: Decimal
    delivery_late: bool
    lines: tuple[InvoiceMatchLine, ...]
    matched_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "exception_codes": list(self.exception_codes),
            "variance_net": str(self.variance_net),
            "delivery_late": self.delivery_late,
            "lines": [line.as_dict() for line in self.lines],
            "matched_at": self.matched_at.isoformat(),
        }


def match_supplier_invoice(
    purchase_order: MatchPurchaseOrder,
    invoice: MatchSupplierInvoice,
    *,
    matched_at: datetime,
) -> InvoiceMatchResult:
    """Compare invoice quantities, prices, totals and delivery against a PO."""

    po_lines = {line.id: line for line in purchase_order.lines}
    exception_codes: list[str] = []
    matched_lines: list[InvoiceMatchLine] = []
    seen: set[UUID] = set()

    for invoice_line in invoice.lines:
        po_line = po_lines.get(invoice_line.purchase_order_line_id)
        if po_line is None:
            exception_codes.append("UNKNOWN_PO_LINE")
            continue
        if invoice_line.purchase_order_line_id in seen:
            exception_codes.append("DUPLICATE_PO_LINE")
            continue
        seen.add(invoice_line.purchase_order_line_id)

        quantity_variance = invoice_line.quantity - po_line.quantity
        price_variance = invoice_line.unit_price_net - po_line.unit_price_net
        line_total_variance = invoice_line.total_net - po_line.total_net
        line_codes: list[str] = []
        if abs(quantity_variance) > QUANTITY_TOLERANCE:
            line_codes.append("QUANTITY_MISMATCH")
        if abs(price_variance) > PRICE_TOLERANCE:
            line_codes.append("PRICE_MISMATCH")
        if abs(line_total_variance) > MONEY_TOLERANCE:
            line_codes.append("LINE_TOTAL_MISMATCH")
        exception_codes.extend(line_codes)
        matched_lines.append(
            InvoiceMatchLine(
                purchase_order_line_id=po_line.id,
                line_number=po_line.line_number,
                description=po_line.description,
                expected_quantity=po_line.quantity,
                actual_quantity=invoice_line.quantity,
                expected_unit_price_net=po_line.unit_price_net,
                actual_unit_price_net=invoice_line.unit_price_net,
                expected_total_net=po_line.total_net,
                actual_total_net=invoice_line.total_net,
                quantity_variance=quantity_variance,
                price_variance=price_variance,
                total_variance=line_total_variance,
                exception_codes=tuple(line_codes),
            )
        )

    variance_net = q2(invoice.total_net - purchase_order.total_net)
    if abs(variance_net) > MONEY_TOLERANCE:
        exception_codes.append("TOTAL_MISMATCH")

    delivery_late = (
        invoice.delivery_date is not None
        and purchase_order.expected_delivery_date is not None
        and invoice.delivery_date > purchase_order.expected_delivery_date
    )
    if delivery_late:
        exception_codes.append("DELIVERY_LATE")

    codes = tuple(dict.fromkeys(exception_codes))
    return InvoiceMatchResult(
        status=SupplierInvoiceStatus.EXCEPTION if codes else SupplierInvoiceStatus.MATCHED,
        exception_codes=codes,
        variance_net=variance_net,
        delivery_late=delivery_late,
        lines=tuple(matched_lines),
        matched_at=matched_at,
    )


@dataclass(frozen=True, slots=True)
class MarginOfferLine:
    quantity: Decimal
    cost_unit_price_net: Decimal


@dataclass(frozen=True, slots=True)
class MarginOffer:
    id: UUID
    offer_number: str
    customer_name: str
    currency: str
    total_net: Decimal
    lines: tuple[MarginOfferLine, ...]


@dataclass(frozen=True, slots=True)
class MarginInvoice:
    total_net: Decimal
    status: SupplierInvoiceStatus
    exception_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarginSummary:
    offer_id: UUID
    offer_number: str
    customer_name: str
    currency: str
    quoted_revenue_net: Decimal
    quoted_cost_net: Decimal
    quoted_margin_net: Decimal
    quoted_margin_rate: Decimal | None
    actual_cost_net: Decimal
    actual_margin_net: Decimal | None
    actual_margin_rate: Decimal | None
    margin_variance_net: Decimal | None
    margin_variance_rate: Decimal | None
    invoice_count: int
    purchase_order_count: int
    exception_count: int
    exception_codes: tuple[str, ...]
    coverage_rate: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": str(self.offer_id),
            "offer_number": self.offer_number,
            "customer_name": self.customer_name,
            "currency": self.currency,
            "quoted_revenue_net": str(self.quoted_revenue_net),
            "quoted_cost_net": str(self.quoted_cost_net),
            "quoted_margin_net": str(self.quoted_margin_net),
            "quoted_margin_rate": (
                str(self.quoted_margin_rate) if self.quoted_margin_rate is not None else None
            ),
            "actual_cost_net": str(self.actual_cost_net),
            "actual_margin_net": (
                str(self.actual_margin_net) if self.actual_margin_net is not None else None
            ),
            "actual_margin_rate": (
                str(self.actual_margin_rate) if self.actual_margin_rate is not None else None
            ),
            "margin_variance_net": (
                str(self.margin_variance_net) if self.margin_variance_net is not None else None
            ),
            "margin_variance_rate": (
                str(self.margin_variance_rate) if self.margin_variance_rate is not None else None
            ),
            "invoice_count": self.invoice_count,
            "purchase_order_count": self.purchase_order_count,
            "exception_count": self.exception_count,
            "exception_codes": list(self.exception_codes),
            "coverage_rate": str(self.coverage_rate) if self.coverage_rate is not None else None,
        }


def calculate_margin(
    offer: MarginOffer,
    invoices: tuple[MarginInvoice, ...],
    *,
    purchase_order_count: int,
) -> MarginSummary:
    """Compare the offer's quoted margin with actual supplier-invoice cost."""

    quoted_cost_net = q2(
        sum(
            (line.quantity * line.cost_unit_price_net for line in offer.lines),
            start=Decimal("0.00"),
        )
    )
    quoted_margin_net = q2(offer.total_net - quoted_cost_net)
    quoted_margin_rate = q4(quoted_margin_net / offer.total_net) if offer.total_net > 0 else None

    actual_invoices = tuple(
        invoice for invoice in invoices if invoice.status != SupplierInvoiceStatus.REJECTED
    )
    actual_cost_net = q2(sum((invoice.total_net for invoice in actual_invoices), start=Decimal("0.00")))
    if actual_invoices:
        computed_margin_net = q2(offer.total_net - actual_cost_net)
        actual_margin_net = computed_margin_net
        actual_margin_rate = q4(computed_margin_net / offer.total_net) if offer.total_net > 0 else None
        margin_variance_net = q2(computed_margin_net - quoted_margin_net)
        margin_variance_rate = (
            q4(actual_margin_rate - quoted_margin_rate)
            if actual_margin_rate is not None and quoted_margin_rate is not None
            else None
        )
    else:
        actual_margin_net = None
        actual_margin_rate = None
        margin_variance_net = None
        margin_variance_rate = None

    exception_codes = tuple(
        dict.fromkeys(
            code
            for invoice in actual_invoices
            for code in invoice.exception_codes
            if invoice.status == SupplierInvoiceStatus.EXCEPTION
        )
    )
    exception_count = sum(
        1 for invoice in actual_invoices if invoice.status == SupplierInvoiceStatus.EXCEPTION
    )
    coverage_rate = q4(actual_cost_net / quoted_cost_net) if actual_invoices and quoted_cost_net > 0 else None
    return MarginSummary(
        offer_id=offer.id,
        offer_number=offer.offer_number,
        customer_name=offer.customer_name,
        currency=offer.currency,
        quoted_revenue_net=offer.total_net,
        quoted_cost_net=quoted_cost_net,
        quoted_margin_net=quoted_margin_net,
        quoted_margin_rate=quoted_margin_rate,
        actual_cost_net=actual_cost_net,
        actual_margin_net=actual_margin_net,
        actual_margin_rate=actual_margin_rate,
        margin_variance_net=margin_variance_net,
        margin_variance_rate=margin_variance_rate,
        invoice_count=len(actual_invoices),
        purchase_order_count=purchase_order_count,
        exception_count=exception_count,
        exception_codes=exception_codes,
        coverage_rate=coverage_rate,
    )
