"""Strict domain schema tests (Tender, SupplierQuote, CustomerOffer)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.models.enums import (
    CustomerOfferStatus,
    GloveSize,
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    SupplierQuoteStatus,
    TenderSourceFormat,
    TenderStatus,
    UnitOfMeasure,
)
from src.models.schemas import (
    CustomerOffer,
    CustomerOfferLine,
    PriceTier,
    SupplierQuote,
    SupplierQuoteLine,
    Tender,
    TenderItem,
    q2,
    q4,
)

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


def make_item(**overrides: object) -> TenderItem:
    data: dict[str, object] = {
        "position_number": "1.1",
        "lot": "Los 1",
        "short_text": "Nitrilhandschuhe puderfrei",
        "quantity": Decimal("12.0000"),
        "unit": UnitOfMeasure.KARTON,
        "glove_size": GloveSize.M,
        "material": MaterialType.NITRILE,
        "color": StandardizedColor.BLUE,
        "thickness_um": Decimal("120.00"),
        "pack_size": 1000,
        "cpv_code": "33141420",
    }
    data.update(overrides)
    return TenderItem(**data)


def make_tender(**overrides: object) -> Tender:
    data: dict[str, object] = {
        "tender_number": "T-2026-001",
        "title": "Rahmenvertrag Reinigungsmittel",
        "client_name": "Stadt Muster",
        "client_contact_email": "einkauf@example.de",
        "procedure": ProcurementProcedure.OPEN_PROCEDURE,
        "source_format": TenderSourceFormat.GAEB_X83,
        "publication_date": date(2026, 1, 2),
        "submission_deadline": NOW + timedelta(days=14),
        "items": [make_item()],
    }
    data.update(overrides)
    return Tender(**data)


def make_quote_line(**overrides: object) -> SupplierQuoteLine:
    data: dict[str, object] = {
        "line_number": 1,
        "description": "Nitrilhandschuhe Gr. M",
        "quantity": Decimal("12.0000"),
        "unit": UnitOfMeasure.KARTON,
        "unit_price_net": Decimal("100.0000"),
        "tiers": [PriceTier(min_quantity=Decimal("50.0000"), unit_price_net=Decimal("95.0000"))],
        "glove_size": GloveSize.M,
    }
    data.update(overrides)
    return SupplierQuoteLine(**data)


def make_quote(**overrides: object) -> SupplierQuote:
    data: dict[str, object] = {
        "supplier_name": "Hygiene Gross GmbH",
        "quote_reference": "ANG-77",
        "lines": [make_quote_line()],
    }
    data.update(overrides)
    return SupplierQuote(**data)


def make_offer_line(**overrides: object) -> CustomerOfferLine:
    cost = Decimal("10.0000")
    margin = Decimal("0.15")
    quantity = Decimal("10.0000")
    unit_price = q4(cost * (Decimal(1) + margin))
    data: dict[str, object] = {
        "position_number": "1.1",
        "description": "Nitrilhandschuhe Gr. M",
        "quantity": quantity,
        "unit": UnitOfMeasure.KARTON,
        "cost_unit_price_net": cost,
        "margin_rate": margin,
        "unit_price_net": unit_price,
        "total_net": q2(quantity * unit_price),
    }
    data.update(overrides)
    return CustomerOfferLine(**data)


def make_offer(**overrides: object) -> CustomerOffer:
    line = make_offer_line()
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    data: dict[str, object] = {
        "offer_number": "AN-2026-0001",
        "customer_name": "Stadt Muster",
        "status": CustomerOfferStatus.DRAFT,
        "lines": [line],
        "margin_rate": Decimal("0.15"),
        "total_net": net,
        "total_vat": vat,
        "total_gross": q2(net + vat),
        "valid_until": date(2026, 12, 31),
    }
    data.update(overrides)
    return CustomerOffer(**data)


# ---------------------------------------------------------------------------
# Tenders
# ---------------------------------------------------------------------------


def test_valid_tender_roundtrip() -> None:
    tender = make_tender()

    assert tender.status is TenderStatus.DRAFT
    assert tender.currency.value == "EUR"
    assert tender.items[0].glove_size is GloveSize.M
    assert tender.model_dump_json()


def test_tender_rejects_raw_string_quantities() -> None:
    with pytest.raises(ValidationError):
        make_item(quantity="12")  # type: ignore[arg-type]


def test_tender_rejects_string_deadline() -> None:
    with pytest.raises(ValidationError):
        make_tender(submission_deadline="2026-02-01T00:00:00Z")


def test_tender_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_tender(unknown_field=True)


def test_tender_rejects_deadline_before_publication() -> None:
    with pytest.raises(ValidationError, match="submission_deadline"):
        make_tender(submission_deadline=datetime(2025, 12, 31, tzinfo=timezone.utc))


def test_tender_rejects_duplicate_positions_per_lot() -> None:
    with pytest.raises(ValidationError, match="duplicate position_number"):
        make_tender(items=[make_item(), make_item()])


def test_tender_deduplicates_cpv_codes() -> None:
    tender = make_tender(cpv_codes=["33141420", "33141420", "90910000-9"])

    assert tender.cpv_codes == ["33141420", "90910000-9"]


# ---------------------------------------------------------------------------
# Supplier quotes
# ---------------------------------------------------------------------------


def test_supplier_quote_defaults_and_tiers() -> None:
    quote = make_quote()

    assert quote.status is SupplierQuoteStatus.RECEIVED
    assert quote.lines[0].tiers[0].unit_price_net == Decimal("95.0000")


def test_supplier_quote_requires_at_least_one_line() -> None:
    with pytest.raises(ValidationError):
        make_quote(lines=[])


def test_supplier_quote_rejects_duplicate_line_numbers() -> None:
    with pytest.raises(ValidationError, match="line_number"):
        make_quote(lines=[make_quote_line(), make_quote_line()])


def test_supplier_quote_tiers_must_be_increasing() -> None:
    with pytest.raises(ValidationError, match="strictly increasing"):
        make_quote_line(
            tiers=[
                PriceTier(min_quantity=Decimal("10.0000"), unit_price_net=Decimal("99.0000")),
                PriceTier(min_quantity=Decimal("10.0000"), unit_price_net=Decimal("95.0000")),
            ]
        )


# ---------------------------------------------------------------------------
# Customer offers
# ---------------------------------------------------------------------------


def test_offer_totals_are_validated() -> None:
    offer = make_offer()

    assert offer.total_net == Decimal("115.00")
    assert offer.total_vat == Decimal("21.85")
    assert offer.total_gross == Decimal("136.85")

    with pytest.raises(ValidationError, match="total_net"):
        make_offer(total_net=Decimal("999.00"), total_vat=Decimal("189.81"), total_gross=Decimal("1188.81"))


def test_offer_line_price_derivation_is_enforced() -> None:
    with pytest.raises(ValidationError, match="unit_price_net"):
        make_offer_line(unit_price_net=Decimal("11.0000"))


def test_offer_requires_hitl_signature_for_approved_status() -> None:
    with pytest.raises(ValidationError, match="HITL approval required"):
        make_offer(status=CustomerOfferStatus.APPROVED)


def test_offer_requires_hitl_signature_for_export() -> None:
    with pytest.raises(ValidationError, match="HITL approval required"):
        make_offer(erpnext_quotation_name="QTN-2026-0001")


def test_offer_accepts_signed_approval() -> None:
    offer = make_offer(
        status=CustomerOfferStatus.APPROVED,
        ed25519_approval_signature="ab" * 64,
        approval_payload_hash="cd" * 32,
        approved_by="C. Mueller",
        approved_at=NOW,
    )

    assert offer.status is CustomerOfferStatus.APPROVED
    assert len(offer.ed25519_approval_signature or "") == 128


def test_offer_rejects_malformed_signature() -> None:
    with pytest.raises(ValidationError):
        make_offer(
            status=CustomerOfferStatus.APPROVED,
            ed25519_approval_signature="not-hex",
            approval_payload_hash="cd" * 32,
            approved_by="C. Mueller",
            approved_at=NOW,
        )


def test_offer_rejects_duplicate_positions() -> None:
    with pytest.raises(ValidationError, match="position_number"):
        make_offer(lines=[make_offer_line(), make_offer_line()])


def test_offer_margin_is_bounded() -> None:
    with pytest.raises(ValidationError):
        make_offer(margin_rate=Decimal("6.0"))
