"""Procurement state-machine, invoice matching and margin tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.models.enums import PurchaseOrderStatus, SupplierInvoiceStatus
from src.services.procurement_service import (
    InvoiceMatchResult,
    MarginInvoice,
    MarginOffer,
    MarginOfferLine,
    MatchInvoiceLine,
    MatchPoLine,
    MatchPurchaseOrder,
    MatchSupplierInvoice,
    PurchaseOrderTransitionError,
    calculate_margin,
    ensure_purchase_order_transition,
    match_supplier_invoice,
)

NOW = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
PO_ID = uuid4()
PO_LINE_ID = uuid4()


def make_po() -> MatchPurchaseOrder:
    return MatchPurchaseOrder(
        id=PO_ID,
        total_net=Decimal("200.00"),
        expected_delivery_date=date(2026, 2, 10),
        lines=(
            MatchPoLine(
                id=PO_LINE_ID,
                line_number=1,
                description="Nitrilhandschuhe",
                quantity=Decimal("10.0000"),
                unit_price_net=Decimal("20.0000"),
                total_net=Decimal("200.00"),
            ),
        ),
    )


def make_invoice(
    *,
    quantity: Decimal = Decimal("10.0000"),
    unit_price_net: Decimal = Decimal("20.0000"),
    total_net: Decimal = Decimal("200.00"),
    delivery_date: date | None = date(2026, 2, 9),
) -> MatchSupplierInvoice:
    return MatchSupplierInvoice(
        id=uuid4(),
        total_net=total_net,
        delivery_date=delivery_date,
        lines=(
            MatchInvoiceLine(
                id=uuid4(),
                purchase_order_line_id=PO_LINE_ID,
                description="Nitrilhandschuhe",
                quantity=quantity,
                unit_price_net=unit_price_net,
                total_net=total_net,
            ),
        ),
    )


def test_purchase_order_state_machine_allows_only_forward_or_cancel() -> None:
    assert ensure_purchase_order_transition(PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.SENT) is None
    ensure_purchase_order_transition(PurchaseOrderStatus.SENT, PurchaseOrderStatus.CONFIRMED)
    ensure_purchase_order_transition(PurchaseOrderStatus.CONFIRMED, PurchaseOrderStatus.DELIVERED)
    ensure_purchase_order_transition(PurchaseOrderStatus.CONFIRMED, PurchaseOrderStatus.CANCELLED)
    ensure_purchase_order_transition(PurchaseOrderStatus.SENT, PurchaseOrderStatus.SENT)

    with pytest.raises(PurchaseOrderTransitionError):
        ensure_purchase_order_transition(PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.DELIVERED)
    with pytest.raises(PurchaseOrderTransitionError):
        ensure_purchase_order_transition(PurchaseOrderStatus.DELIVERED, PurchaseOrderStatus.CANCELLED)


def test_matching_exact_invoice_is_clean() -> None:
    result = match_supplier_invoice(make_po(), make_invoice(), matched_at=NOW)

    assert isinstance(result, InvoiceMatchResult)
    assert result.status == SupplierInvoiceStatus.MATCHED
    assert result.exception_codes == ()
    assert result.variance_net == Decimal("0.00")
    assert result.lines[0].quantity_variance == Decimal("0.0000")


def test_matching_flags_quantity_price_total_and_delivery_errors() -> None:
    invoice = make_invoice(
        quantity=Decimal("8.0000"),
        unit_price_net=Decimal("21.0000"),
        total_net=Decimal("168.00"),
        delivery_date=date(2026, 2, 12),
    )

    result = match_supplier_invoice(make_po(), invoice, matched_at=NOW)

    assert result.status == SupplierInvoiceStatus.EXCEPTION
    assert {
        "QUANTITY_MISMATCH",
        "PRICE_MISMATCH",
        "LINE_TOTAL_MISMATCH",
        "TOTAL_MISMATCH",
        "DELIVERY_LATE",
    } <= set(result.exception_codes)
    assert result.variance_net == Decimal("-32.00")
    assert result.delivery_late is True


def test_matching_flags_unknown_purchase_order_line() -> None:
    invoice = MatchSupplierInvoice(
        id=uuid4(),
        total_net=Decimal("200.00"),
        delivery_date=None,
        lines=(
            MatchInvoiceLine(
                id=uuid4(),
                purchase_order_line_id=uuid4(),
                description="Unknown",
                quantity=Decimal("10"),
                unit_price_net=Decimal("20"),
                total_net=Decimal("200.00"),
            ),
        ),
    )

    result = match_supplier_invoice(make_po(), invoice, matched_at=NOW)

    assert "UNKNOWN_PO_LINE" in result.exception_codes


def make_margin_offer() -> MarginOffer:
    return MarginOffer(
        id=uuid4(),
        offer_number="E2E-100",
        customer_name="Stadt Muster",
        currency="EUR",
        total_net=Decimal("1000.00"),
        lines=(
            MarginOfferLine(
                quantity=Decimal("10.0000"),
                cost_unit_price_net=Decimal("60.0000"),
            ),
        ),
    )


def test_margin_compares_quoted_and_actual_supplier_cost() -> None:
    summary = calculate_margin(
        make_margin_offer(),
        (
            MarginInvoice(
                total_net=Decimal("650.00"),
                status=SupplierInvoiceStatus.MATCHED,
                exception_codes=(),
            ),
        ),
        purchase_order_count=1,
    )

    assert summary.quoted_cost_net == Decimal("600.00")
    assert summary.quoted_margin_net == Decimal("400.00")
    assert summary.quoted_margin_rate == Decimal("0.4000")
    assert summary.actual_cost_net == Decimal("650.00")
    assert summary.actual_margin_net == Decimal("350.00")
    assert summary.actual_margin_rate == Decimal("0.3500")
    assert summary.margin_variance_net == Decimal("-50.00")
    assert summary.margin_variance_rate == Decimal("-0.0500")
    assert summary.coverage_rate == Decimal("1.0833")


def test_margin_is_unavailable_until_an_invoice_exists_and_ignores_rejected() -> None:
    empty = calculate_margin(make_margin_offer(), (), purchase_order_count=0)
    assert empty.actual_cost_net == Decimal("0.00")
    assert empty.actual_margin_net is None
    assert empty.actual_margin_rate is None

    rejected = calculate_margin(
        make_margin_offer(),
        (
            MarginInvoice(
                total_net=Decimal("900.00"),
                status=SupplierInvoiceStatus.REJECTED,
                exception_codes=("TOTAL_MISMATCH",),
            ),
        ),
        purchase_order_count=1,
    )
    assert rejected.invoice_count == 0
    assert rejected.actual_margin_net is None
    assert rejected.exception_count == 0


def test_margin_records_exception_codes_from_matching() -> None:
    summary = calculate_margin(
        make_margin_offer(),
        (
            MarginInvoice(
                total_net=Decimal("650.00"),
                status=SupplierInvoiceStatus.EXCEPTION,
                exception_codes=("TOTAL_MISMATCH", "DELIVERY_LATE"),
            ),
        ),
        purchase_order_count=1,
    )

    assert summary.exception_count == 1
    assert summary.exception_codes == ("TOTAL_MISMATCH", "DELIVERY_LATE")
