"""Deterministic unit conversion tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.models.enums import UnitOfMeasure
from src.models.schemas import PriceTier, SupplierQuoteLine
from src.services.pricing_service import PricingService
from src.services.unit_converter import (
    UnitConversionError,
    UnitConverter,
    conversion_factor,
    convert_quantity,
    convert_unit_price,
)


def make_line(**overrides: object) -> SupplierQuoteLine:
    data: dict[str, object] = {
        "line_number": 1,
        "description": "Nitrilhandschuhe",
        "quantity": Decimal("5.0000"),
        "unit": UnitOfMeasure.KARTON,
        "unit_price_net": Decimal("10.0000"),
        "pack_size": 1000,
    }
    data.update(overrides)
    return SupplierQuoteLine(**data)


def test_carton_to_piece_factor_and_prices() -> None:
    result = conversion_factor(UnitOfMeasure.KARTON, UnitOfMeasure.STUECK, pack_size=1000)

    assert result.factor == Decimal("1000")
    assert result.quantity(Decimal("5")) == Decimal("5000.0000")
    assert result.unit_price(Decimal("10.0000")) == Decimal("0.0100")
    assert convert_quantity(
        Decimal("2"), UnitOfMeasure.KARTON, UnitOfMeasure.STUECK, pack_size=1000
    ) == Decimal("2000.0000")
    assert convert_unit_price(
        Decimal("10"), UnitOfMeasure.KARTON, UnitOfMeasure.STUECK, pack_size=1000
    ) == Decimal("0.0100")


def test_converted_line_rewrites_tiers_moq_and_attributes() -> None:
    line = make_line(
        moq=Decimal("2.0000"),
        tiers=[PriceTier(min_quantity=Decimal("2.0000"), unit_price_net=Decimal("9.0000"))],
    )

    converted = UnitConverter().convert_line(line, UnitOfMeasure.STUECK)

    assert converted.unit is UnitOfMeasure.STUECK
    assert converted.quantity == Decimal("5000.0000")
    assert converted.unit_price_net == Decimal("0.0100")
    assert converted.moq == Decimal("2000.0000")
    assert converted.tiers[0].min_quantity == Decimal("2000.0000")
    assert converted.tiers[0].unit_price_net == Decimal("0.0090")
    assert converted.pack_size is None
    assert converted.attributes["converted_from_unit"] == "KARTON"
    assert converted.attributes["conversion_factor"] == "1000"


def test_piece_to_carton_uses_explicit_units_per_carton() -> None:
    result = conversion_factor(
        UnitOfMeasure.STUECK,
        UnitOfMeasure.KARTON,
        attributes={"units_per_carton": 1000},
    )

    assert result.factor == Decimal("0.001")
    assert result.quantity(Decimal("5000")) == Decimal("5.0000")
    assert result.unit_price(Decimal("0.0100")) == Decimal("10.0000")


def test_roll_to_meter_uses_roll_length() -> None:
    result = conversion_factor(
        UnitOfMeasure.ROLLE,
        UnitOfMeasure.METER,
        attributes={"meters_per_roll": 50},
    )

    assert result.factor == Decimal("50")
    assert result.quantity(Decimal("2")) == Decimal("100.0000")


def test_identity_and_missing_factor() -> None:
    line = make_line()
    assert UnitConverter().convert_line(line, UnitOfMeasure.KARTON) is line

    with pytest.raises(UnitConversionError, match="KARTON conversion requires"):
        conversion_factor(UnitOfMeasure.KARTON, UnitOfMeasure.STUECK)
    with pytest.raises(UnitConversionError, match="different physical dimensions"):
        conversion_factor(UnitOfMeasure.KILOGRAMM, UnitOfMeasure.STUECK)
    with pytest.raises(UnitConversionError, match="NOT_APPLICABLE"):
        conversion_factor(UnitOfMeasure.NOT_APPLICABLE, UnitOfMeasure.STUECK)


def test_pricing_applies_converted_cost_per_piece() -> None:
    converted = UnitConverter().convert_line(make_line(), UnitOfMeasure.STUECK)

    priced = PricingService(default_vat_rate=Decimal("0.19")).price_line(
        converted,
        quantity=Decimal("5000"),
        margin_rate=Decimal("0.15"),
    )

    assert priced.unit is UnitOfMeasure.STUECK
    assert priced.cost_unit_price_net == Decimal("0.0100")
    assert priced.unit_price_net == Decimal("0.0115")
    assert priced.total_net == Decimal("57.50")
