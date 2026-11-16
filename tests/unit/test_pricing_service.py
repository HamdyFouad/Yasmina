"""Deterministic pricing-engine tests (pure Decimal)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.models.enums import UnitOfMeasure
from src.models.schemas import PriceTier, SupplierQuoteLine
from src.services.pricing_service import (
    PricingError,
    PricingService,
    calculate_unit_price,
    round_quantity,
    select_tier_price,
)


def make_line(**overrides: object) -> SupplierQuoteLine:
    data: dict[str, object] = {
        "line_number": 1,
        "description": "Nitrilhandschuhe Gr. M",
        "quantity": Decimal("10.0000"),
        "unit": UnitOfMeasure.KARTON,
        "unit_price_net": Decimal("10.0000"),
    }
    data.update(overrides)
    return SupplierQuoteLine(**data)


def test_base_price_margin_and_line_total() -> None:
    service = PricingService()
    line = make_line()

    priced = service.price_line(line, margin_rate=Decimal("0.15"))

    assert priced.unit_price_net == Decimal("11.5000")
    assert priced.total_net == Decimal("115.00")
    assert priced.applied_tier_quantity is None


def test_volume_tier_boundaries() -> None:
    line = make_line(
        tiers=[
            PriceTier(min_quantity=Decimal("50.0000"), unit_price_net=Decimal("9.0000")),
            PriceTier(min_quantity=Decimal("100.0000"), unit_price_net=Decimal("8.0000")),
        ]
    )

    assert select_tier_price(line, Decimal("49.0000")) == (Decimal("10.0000"), None)
    assert select_tier_price(line, Decimal("50.0000")) == (Decimal("9.0000"), Decimal("50.0000"))
    assert select_tier_price(line, Decimal("100.0000")) == (Decimal("8.0000"), Decimal("100.0000"))


def test_quantity_rounding_to_moq_and_pack() -> None:
    assert round_quantity(Decimal("23"), moq=Decimal("10")) == Decimal("30")
    assert round_quantity(Decimal("13"), pack_size=12) == Decimal("24")
    assert round_quantity(Decimal("1"), moq=Decimal("5"), pack_size=12) == Decimal("12")


def test_moq_rounding_sets_billed_quantity_and_tier() -> None:
    line = make_line(
        quantity=Decimal("12.0000"),
        moq=Decimal("50.0000"),
        tiers=[PriceTier(min_quantity=Decimal("50.0000"), unit_price_net=Decimal("9.0000"))],
    )
    service = PricingService()

    without_moq = service.price_line(line, margin_rate=Decimal("0"))
    with_moq = service.price_line(line, margin_rate=Decimal("0"), round_to_moq=True)

    assert without_moq.billed_quantity == Decimal("12.0000")
    assert without_moq.unit_price_net == Decimal("10.0000")
    assert with_moq.billed_quantity == Decimal("50")
    assert with_moq.unit_price_net == Decimal("9.0000")
    assert with_moq.total_net == Decimal("450.00")


def test_offer_totals_with_vat() -> None:
    first = make_line(quantity=Decimal("2.0000"), unit_price_net=Decimal("10.0000"))
    second = make_line(
        line_number=2,
        description="Papierhandtücher",
        quantity=Decimal("3.0000"),
        unit_price_net=Decimal("20.0000"),
    )
    service = PricingService()

    pricing = service.price_offer(
        [first, second],
        margin_rate=Decimal("0.10"),
        vat_rate=Decimal("0.19"),
    )

    assert pricing.total_net == Decimal("88.00")  # 22.00 + 66.00
    assert pricing.total_vat == Decimal("16.72")
    assert pricing.total_gross == Decimal("104.72")


def test_money_rounds_half_up() -> None:
    assert calculate_unit_price(Decimal("1.1111"), Decimal("0.10")) == Decimal("1.2222")
    assert calculate_unit_price(Decimal("0.3333"), Decimal("0.15")) == Decimal("0.3833")


def test_freight_and_pallet_charges_are_included_in_totals() -> None:
    service = PricingService()
    line = make_line(quantity=Decimal("10.0000"), unit_price_net=Decimal("10.0000"))

    pricing = service.price_offer(
        [line],
        margin_rate=Decimal("0.10"),
        vat_rate=Decimal("0.19"),
        freight_flat_net=Decimal("25.00"),
        pallet_unit_net=Decimal("12.50"),
        pallet_count=3,
    )

    assert pricing.freight_net == Decimal("25.00")
    assert pricing.pallet_net == Decimal("37.50")
    assert pricing.pallet_count == 3
    assert [charge.kind for charge in pricing.charges] == ["freight", "pallet"]
    assert pricing.charges[0].description == "Freight (flat)"
    assert pricing.charges[1].quantity == Decimal("3")
    assert pricing.total_net == Decimal("172.50")
    assert pricing.total_vat == Decimal("32.78")
    assert pricing.total_gross == Decimal("205.28")


def test_charge_calculation_rejects_invalid_values() -> None:
    service = PricingService()
    with pytest.raises(PricingError, match="freight"):
        service.calculate_charges(freight_flat_net=Decimal("-1.00"))
    with pytest.raises(PricingError, match="pallet"):
        service.calculate_charges(pallet_unit_net=Decimal("-1.00"))
    with pytest.raises(PricingError, match="pallet_count"):
        service.calculate_charges(pallet_count=-1)
    with pytest.raises(PricingError, match="pallet_count"):
        service.calculate_charges(pallet_count=True)  # type: ignore[arg-type]


def test_pricing_rejects_float_and_invalid_ranges() -> None:
    service = PricingService()
    line = make_line()

    with pytest.raises(PricingError, match="Decimal"):
        service.price_line(line, quantity=2.5)  # type: ignore[arg-type]
    with pytest.raises(PricingError, match="margin_rate"):
        calculate_unit_price(Decimal("10"), Decimal("6"))
    with pytest.raises(PricingError, match="quantity"):
        round_quantity(Decimal("0"))
    with pytest.raises(PricingError, match="at least one"):
        service.price_offer([])
