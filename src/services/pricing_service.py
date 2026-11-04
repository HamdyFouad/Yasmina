"""Deterministic pricing engine for Project Yasmina.

Everything here is pure ``Decimal`` arithmetic: no floats, no LLM calls, no I/O.
The service applies supplier volume tiers, optional MOQ/pack rounding, margin
and German VAT, and returns cent-rounded totals.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Sequence

from src.models.enums import UnitOfMeasure
from src.models.schemas import SupplierQuoteLine, q2, q4

__all__ = [
    "OfferPricing",
    "PricedLine",
    "PricingError",
    "PricingService",
    "calculate_unit_price",
    "round_quantity",
    "select_tier_price",
]

_ZERO = Decimal("0.00")
_ONE = Decimal("1")


class PricingError(ValueError):
    """Raised when pricing input is invalid or non-deterministic."""


@dataclass(frozen=True, slots=True)
class PricedLine:
    """One priced line, including the tier/MOQ decisions that produced it."""

    requested_quantity: Decimal
    billed_quantity: Decimal
    unit: UnitOfMeasure
    cost_unit_price_net: Decimal
    margin_rate: Decimal
    unit_price_net: Decimal
    total_net: Decimal
    applied_tier_quantity: Decimal | None = None
    moq: Decimal | None = None
    pack_size: int | None = None


@dataclass(frozen=True, slots=True)
class OfferPricing:
    """Totals for a set of priced lines."""

    lines: tuple[PricedLine, ...]
    total_net: Decimal
    vat_rate: Decimal
    total_vat: Decimal
    total_gross: Decimal


def _decimal(value: Decimal | int, name: str) -> Decimal:
    if isinstance(value, bool):
        raise PricingError(f"{name} must be a Decimal, not bool")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    raise PricingError(f"{name} must be a Decimal or int, not {type(value).__name__}")


def _ceil_multiple(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise PricingError("rounding step must be positive")
    return (quantity / step).to_integral_value(rounding=ROUND_CEILING) * step


def round_quantity(
    quantity: Decimal | int,
    *,
    moq: Decimal | int | None = None,
    pack_size: int | None = None,
) -> Decimal:
    """Round a quantity up to MOQ and/or full-pack multiples.

    MOQ is applied first, then the pack size. Passing neither leaves the
    quantity unchanged.
    """

    result = _decimal(quantity, "quantity")
    if result <= 0:
        raise PricingError("quantity must be positive")

    if moq is not None:
        moq_value = _decimal(moq, "moq")
        if moq_value <= 0:
            raise PricingError("moq must be positive")
        result = _ceil_multiple(result, moq_value)

    if pack_size is not None:
        if pack_size <= 0:
            raise PricingError("pack_size must be positive")
        result = _ceil_multiple(result, Decimal(pack_size))

    return result


def select_tier_price(
    line: SupplierQuoteLine,
    quantity: Decimal,
) -> tuple[Decimal, Decimal | None]:
    """Return ``(unit_price, applied_min_quantity)`` for a quantity.

    The highest qualifying volume tier wins; below the first tier the base
    price applies. Tiers are validated as strictly increasing by the schema.
    """

    if quantity <= 0:
        raise PricingError("quantity must be positive")

    applied: Decimal | None = None
    price = _decimal(line.unit_price_net, "unit_price_net")
    for tier in sorted(line.tiers, key=lambda item: item.min_quantity):
        if quantity >= tier.min_quantity:
            price = _decimal(tier.unit_price_net, "tier unit_price_net")
            applied = tier.min_quantity
    return price, applied


def calculate_unit_price(
    cost_unit_price_net: Decimal,
    margin_rate: Decimal,
) -> Decimal:
    """Calculate a customer unit price from cost + margin, rounded to 4 dp."""

    cost = _decimal(cost_unit_price_net, "cost_unit_price_net")
    margin = _decimal(margin_rate, "margin_rate")
    if cost < 0:
        raise PricingError("cost_unit_price_net must not be negative")
    if margin < 0 or margin > 5:
        raise PricingError("margin_rate must be between 0 and 5")

    return q4(cost * (_ONE + margin))


class PricingService:
    """Apply tiers, MOQ/pack rounding, margin and VAT deterministically."""

    def __init__(
        self,
        *,
        default_vat_rate: Decimal = Decimal("0.19"),
        default_margin_rate: Decimal = Decimal("0.15"),
    ) -> None:
        vat = _decimal(default_vat_rate, "default_vat_rate")
        margin = _decimal(default_margin_rate, "default_margin_rate")
        if vat < 0 or vat > 1:
            raise PricingError("default_vat_rate must be between 0 and 1")
        if margin < 0 or margin > 5:
            raise PricingError("default_margin_rate must be between 0 and 5")
        self.default_vat_rate = vat
        self.default_margin_rate = margin

    def price_line(
        self,
        line: SupplierQuoteLine,
        *,
        quantity: Decimal | int | None = None,
        margin_rate: Decimal | int | None = None,
        round_to_moq: bool = False,
    ) -> PricedLine:
        requested = _decimal(quantity if quantity is not None else line.quantity, "quantity")
        if requested <= 0:
            raise PricingError("quantity must be positive")

        margin = _decimal(
            margin_rate if margin_rate is not None else self.default_margin_rate,
            "margin_rate",
        )
        moq = _decimal(line.moq, "moq") if line.moq is not None else None
        billed = (
            round_quantity(requested, moq=moq, pack_size=line.pack_size)
            if round_to_moq
            else requested
        )
        cost, applied_tier = select_tier_price(line, billed)
        unit_price = calculate_unit_price(cost, margin)
        total = q2(billed * unit_price)

        return PricedLine(
            requested_quantity=requested,
            billed_quantity=billed,
            unit=line.unit,
            cost_unit_price_net=cost,
            margin_rate=margin,
            unit_price_net=unit_price,
            total_net=total,
            applied_tier_quantity=applied_tier,
            moq=moq,
            pack_size=line.pack_size,
        )

    def price_offer(
        self,
        lines: Sequence[SupplierQuoteLine],
        *,
        margin_rate: Decimal | int | None = None,
        vat_rate: Decimal | int | None = None,
        quantities: Sequence[Decimal | int] | None = None,
        round_to_moq: bool = False,
    ) -> OfferPricing:
        """Price a sequence of supplier lines into an offer total."""

        if not lines:
            raise PricingError("at least one supplier quote line is required")
        if quantities is not None and len(quantities) != len(lines):
            raise PricingError("quantities length must match lines length")

        vat = _decimal(vat_rate if vat_rate is not None else self.default_vat_rate, "vat_rate")
        if vat < 0 or vat > 1:
            raise PricingError("vat_rate must be between 0 and 1")

        margin = _decimal(
            margin_rate if margin_rate is not None else self.default_margin_rate,
            "margin_rate",
        )
        priced: list[PricedLine] = []
        for index, line in enumerate(lines):
            quantity = quantities[index] if quantities is not None else None
            priced.append(
                self.price_line(
                    line,
                    quantity=quantity,
                    margin_rate=margin,
                    round_to_moq=round_to_moq,
                )
            )

        net = q2(sum((line.total_net for line in priced), start=_ZERO))
        total_vat = q2(net * vat)
        total_gross = q2(net + total_vat)
        return OfferPricing(
            lines=tuple(priced),
            total_net=net,
            vat_rate=vat,
            total_vat=total_vat,
            total_gross=total_gross,
        )
