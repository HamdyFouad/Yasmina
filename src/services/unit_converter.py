"""Deterministic unit conversion for supplier quotes and tender positions.

Only conversions with an explicit, auditable packaging factor are performed.
No density, weight or volume assumptions are invented. The canonical count
units form a hierarchy below ``STUECK``; length uses ``METER``. Mass, volume and
area units are only valid when both sides use the same unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from src.models.enums import UnitOfMeasure
from src.models.schemas import PriceTier, SupplierQuoteLine, q4

__all__ = [
    "ConversionResult",
    "UnitConversionError",
    "UnitConverter",
    "conversion_factor",
    "convert_line",
    "convert_quantity",
    "convert_unit_price",
]

_ONE = Decimal(1)
_COUNT_UNITS = frozenset(
    {UnitOfMeasure.STUECK, UnitOfMeasure.PACK, UnitOfMeasure.KARTON, UnitOfMeasure.PALETTE}
)
_LENGTH_UNITS = frozenset({UnitOfMeasure.METER, UnitOfMeasure.ROLLE})
_SINGLETON_UNITS = frozenset(
    {
        UnitOfMeasure.KILOGRAMM,
        UnitOfMeasure.LITER,
        UnitOfMeasure.QUADRATMETER,
    }
)


class UnitConversionError(ValueError):
    """Raised when no auditable conversion factor exists."""


@dataclass(frozen=True, slots=True)
class ConversionResult:
    source: UnitOfMeasure
    target: UnitOfMeasure
    factor: Decimal

    @property
    def identity(self) -> bool:
        return self.factor == _ONE

    def quantity(self, value: Decimal) -> Decimal:
        return q4(value * self.factor)

    def unit_price(self, value: Decimal) -> Decimal:
        return q4(value / self.factor)


def _positive(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise UnitConversionError(f"{name} must be a positive number")
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise UnitConversionError(f"{name} must be a positive number") from exc
    if not number.is_finite() or number <= 0:
        raise UnitConversionError(f"{name} must be a positive number")
    return number


def _attribute(attributes: Mapping[str, Any], *names: str) -> Decimal | None:
    for name in names:
        if name in attributes and attributes[name] is not None:
            return _positive(attributes[name], name)
    return None


def _carton_factor(
    attributes: Mapping[str, Any],
    *,
    pack_size: Decimal | None,
) -> Decimal | None:
    explicit = _attribute(attributes, "units_per_carton", "pieces_per_carton", "carton_pack_size")
    if explicit is not None:
        return explicit
    if pack_size is not None:
        return pack_size
    units_per_pack = _attribute(attributes, "units_per_pack", "pieces_per_pack")
    packs_per_carton = _attribute(attributes, "packs_per_carton")
    if units_per_pack is not None and packs_per_carton is not None:
        return units_per_pack * packs_per_carton
    return None


def _palette_factor(attributes: Mapping[str, Any], carton_factor: Decimal | None) -> Decimal | None:
    explicit = _attribute(attributes, "units_per_palette", "pieces_per_palette")
    if explicit is not None:
        return explicit
    cartons_per_palette = _attribute(attributes, "cartons_per_palette")
    if cartons_per_palette is not None and carton_factor is not None:
        return cartons_per_palette * carton_factor
    return None


def _source_base_factor(
    unit: UnitOfMeasure,
    *,
    attributes: Mapping[str, Any],
    pack_size: Decimal | None,
) -> Decimal:
    if unit is UnitOfMeasure.STUECK:
        return _ONE
    if unit is UnitOfMeasure.PACK:
        value = _attribute(attributes, "units_per_pack", "pieces_per_pack")
        return value if value is not None else _require(pack_size, "PACK conversion requires pack_size or units_per_pack")
    if unit is UnitOfMeasure.KARTON:
        value = _carton_factor(attributes, pack_size=pack_size)
        return _require(value, "KARTON conversion requires pack_size, units_per_carton or units_per_pack+packs_per_carton")
    if unit is UnitOfMeasure.PALETTE:
        cartons = _carton_factor(attributes, pack_size=pack_size)
        value = _palette_factor(attributes, cartons)
        return _require(value, "PALETTE conversion requires units_per_palette or cartons_per_palette")
    if unit is UnitOfMeasure.METER:
        return _ONE
    if unit is UnitOfMeasure.ROLLE:
        value = _attribute(attributes, "meters_per_roll", "length_m")
        return _require(value, "ROLLE conversion requires meters_per_roll")
    if unit in _SINGLETON_UNITS:
        return _ONE
    raise UnitConversionError(f"unit {unit.value} cannot be used as a conversion source")


def _target_base_factor(
    unit: UnitOfMeasure,
    *,
    attributes: Mapping[str, Any],
    carton_factor: Decimal | None,
) -> Decimal:
    if unit is UnitOfMeasure.STUECK:
        return _ONE
    if unit is UnitOfMeasure.PACK:
        value = _attribute(attributes, "units_per_pack", "pieces_per_pack")
        return _require(value, "target PACK conversion requires units_per_pack")
    if unit is UnitOfMeasure.KARTON:
        value = _carton_factor(attributes, pack_size=None)
        return _require(value, "target KARTON conversion requires units_per_carton or units_per_pack+packs_per_carton")
    if unit is UnitOfMeasure.PALETTE:
        value = _palette_factor(attributes, carton_factor)
        return _require(value, "target PALETTE conversion requires units_per_palette or cartons_per_palette")
    if unit is UnitOfMeasure.METER:
        return _ONE
    if unit is UnitOfMeasure.ROLLE:
        value = _attribute(attributes, "meters_per_roll", "length_m")
        return _require(value, "target ROLLE conversion requires meters_per_roll")
    if unit in _SINGLETON_UNITS:
        return _ONE
    raise UnitConversionError(f"unit {unit.value} cannot be used as a conversion target")


def _require(value: Decimal | None, message: str) -> Decimal:
    if value is None:
        raise UnitConversionError(message)
    return value


def _dimension(unit: UnitOfMeasure) -> str:
    if unit in _COUNT_UNITS:
        return "count"
    if unit in _LENGTH_UNITS:
        return "length"
    if unit is UnitOfMeasure.KILOGRAMM:
        return "mass"
    if unit is UnitOfMeasure.LITER:
        return "volume"
    if unit is UnitOfMeasure.QUADRATMETER:
        return "area"
    return "unknown"


def _factor_from_attributes(
    source: UnitOfMeasure,
    target: UnitOfMeasure,
    *,
    pack_size: Decimal | None,
    attributes: Mapping[str, Any],
) -> Decimal:
    if source is target:
        return _ONE
    if source is UnitOfMeasure.NOT_APPLICABLE or target is UnitOfMeasure.NOT_APPLICABLE:
        raise UnitConversionError("NOT_APPLICABLE units cannot be converted")
    if _dimension(source) != _dimension(target):
        raise UnitConversionError(
            f"cannot convert {source.value} to {target.value}: different physical dimensions"
        )

    carton = _carton_factor(attributes, pack_size=pack_size)
    source_base = _source_base_factor(source, attributes=attributes, pack_size=pack_size)
    target_base = _target_base_factor(target, attributes=attributes, carton_factor=carton)
    factor = source_base / target_base
    if factor <= 0 or not factor.is_finite():
        raise UnitConversionError(f"invalid conversion factor for {source.value} -> {target.value}")
    return factor


def conversion_factor(
    source: UnitOfMeasure,
    target: UnitOfMeasure,
    *,
    pack_size: int | Decimal | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> ConversionResult:
    """Return how many ``target`` units one ``source`` unit represents."""

    pack_value = _positive(pack_size, "pack_size") if pack_size is not None else None
    factor = _factor_from_attributes(
        source,
        target,
        pack_size=pack_value,
        attributes=attributes or {},
    )
    return ConversionResult(source=source, target=target, factor=factor)


def convert_quantity(
    quantity: Decimal | int,
    source: UnitOfMeasure,
    target: UnitOfMeasure,
    *,
    pack_size: int | Decimal | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Decimal:
    result = conversion_factor(source, target, pack_size=pack_size, attributes=attributes)
    return result.quantity(Decimal(quantity))


def convert_unit_price(
    unit_price: Decimal,
    source: UnitOfMeasure,
    target: UnitOfMeasure,
    *,
    pack_size: int | Decimal | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Decimal:
    result = conversion_factor(source, target, pack_size=pack_size, attributes=attributes)
    return result.unit_price(unit_price)


class UnitConverter:
    """Convert a supplier quote line into the tender's unit."""

    def factor_for_line(self, line: SupplierQuoteLine, target: UnitOfMeasure) -> ConversionResult:
        return conversion_factor(
            line.unit,
            target,
            pack_size=line.pack_size,
            attributes=line.attributes,
        )

    def convert_quantity(
        self,
        quantity: Decimal | int,
        source: UnitOfMeasure,
        target: UnitOfMeasure,
        *,
        pack_size: int | Decimal | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Decimal:
        return convert_quantity(
            quantity,
            source,
            target,
            pack_size=pack_size,
            attributes=attributes,
        )

    def convert_unit_price(
        self,
        unit_price: Decimal,
        source: UnitOfMeasure,
        target: UnitOfMeasure,
        *,
        pack_size: int | Decimal | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Decimal:
        return convert_unit_price(
            unit_price,
            source,
            target,
            pack_size=pack_size,
            attributes=attributes,
        )

    def convert(
        self,
        line: SupplierQuoteLine,
        target: UnitOfMeasure,
    ) -> SupplierQuoteLine:
        return self.convert_line(line, target)

    def convert_line(self, line: SupplierQuoteLine, target: UnitOfMeasure) -> SupplierQuoteLine:
        result = self.factor_for_line(line, target)
        if result.identity:
            return line

        converted_quantity = q4(line.quantity * result.factor)
        if converted_quantity <= 0:
            raise UnitConversionError(
                f"quantity {line.quantity} {line.unit.value} becomes zero in {target.value}"
            )
        attributes = dict(line.attributes)
        attributes.update(
            {
                "converted_from_unit": line.unit.value,
                "conversion_factor": str(result.factor),
            }
        )
        tiers = [
            PriceTier(
                min_quantity=q4(tier.min_quantity * result.factor),
                unit_price_net=q4(tier.unit_price_net / result.factor),
            )
            for tier in line.tiers
            if q4(tier.min_quantity * result.factor) > 0
        ]
        return line.model_copy(
            update={
                "unit": target,
                "quantity": converted_quantity,
                "unit_price_net": q4(line.unit_price_net / result.factor),
                "moq": q4(line.moq * result.factor) if line.moq is not None else None,
                "tiers": tiers,
                "pack_size": None,
                "attributes": attributes,
            }
        )


def convert_line(line: SupplierQuoteLine, target: UnitOfMeasure) -> SupplierQuoteLine:
    """Convenience wrapper around :class:`UnitConverter`."""

    return UnitConverter().convert_line(line, target)
