"""Offer repository mapping tests (no database required)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from src.models.enums import CustomerOfferStatus, UnitOfMeasure
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.repositories.offer_repository import OfferRepositoryConflict, offer_to_orm, orm_to_offer
from src.services.approval_service import generate_keypair, sign_offer

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


def make_offer() -> CustomerOffer:
    cost = Decimal("10.0000")
    margin = Decimal("0.15")
    unit_price = q4(cost * (Decimal(1) + margin))
    line = CustomerOfferLine(
        position_number="1.1",
        description="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
        cost_unit_price_net=cost,
        margin_rate=margin,
        unit_price_net=unit_price,
        total_net=q2(Decimal("10.0000") * unit_price),
    )
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    return CustomerOffer(
        offer_number="AN-2026-0001",
        customer_name="Stadt Muster",
        customer_contact_email="einkauf@example.com",
        status=CustomerOfferStatus.DRAFT,
        lines=[line],
        margin_rate=margin,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
        owner="sales-user",
        invoice_artifacts={"xrechnung": {"object_name": "offers/AN/invoice.xml"}},
    )


def test_offer_orm_roundtrip_preserves_owner_and_artifacts() -> None:
    private_key, _ = generate_keypair()
    offer = sign_offer(make_offer(), private_key, approver="Tester", approved_at=NOW)

    orm = offer_to_orm(offer)
    restored = orm_to_offer(orm)

    assert restored.id == offer.id
    assert restored.offer_number == offer.offer_number
    assert restored.owner == "sales-user"
    assert restored.invoice_artifacts == {
        "xrechnung": {"object_name": "offers/AN/invoice.xml"}
    }
    assert restored.status is CustomerOfferStatus.APPROVED
    assert restored.lines[0].id == offer.lines[0].id
    assert restored.lines[0].unit is UnitOfMeasure.KARTON
    assert orm.lines[0].offer_id == offer.id


def test_conflict_error_is_exposed() -> None:
    assert issubclass(OfferRepositoryConflict, ValueError)
