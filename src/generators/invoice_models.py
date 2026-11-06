"""Invoice domain models shared by ZUGFeRD and XRechnung generators."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from src.models.enums import Currency, UnitOfMeasure
from src.models.schemas import q2

__all__ = ["Invoice", "InvoiceLine", "InvoiceParty"]

VatRate = Annotated[Decimal, Field(ge=0, le=1)]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class InvoiceParty(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    street: str | None = Field(default=None, max_length=256)
    city: str | None = Field(default=None, max_length=128)
    postal_code: str | None = Field(default=None, max_length=32)
    country_code: str = Field(default="DE", min_length=2, max_length=2)
    vat_id: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    contact_name: str | None = Field(default=None, max_length=256)
    telephone: str | None = Field(default=None, max_length=64)
    endpoint_id: str | None = Field(default=None, max_length=256)
    endpoint_scheme: str = Field(default="EM", min_length=2, max_length=8)


class InvoiceLine(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    line_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=2000)
    quantity: Decimal = Field(gt=0)
    unit: UnitOfMeasure
    unit_price: Decimal = Field(ge=0)
    line_total: Money
    vat_rate: VatRate = Decimal("0.19")

    @model_validator(mode="after")
    def _check_line_total(self) -> InvoiceLine:
        expected = q2(self.quantity * self.unit_price)
        if self.line_total != expected:
            raise ValueError(f"line_total {self.line_total} does not equal qty*price {expected}")
        return self


class Invoice(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    invoice_number: str = Field(min_length=1, max_length=128)
    issue_date: date
    due_date: date
    delivery_date: date | None = None
    currency: Currency = Currency.EUR

    seller: InvoiceParty
    buyer: InvoiceParty
    buyer_reference: str | None = Field(default=None, max_length=46)
    order_reference: str | None = Field(default=None, max_length=128)

    lines: list[InvoiceLine] = Field(min_length=1)
    total_net: Money
    total_vat: Money
    total_gross: Money

    payment_terms: str | None = Field(default=None, max_length=512)
    iban: str | None = Field(default=None, max_length=34)

    @model_validator(mode="after")
    def _check_totals(self) -> Invoice:
        net = q2(sum((line.line_total for line in self.lines), start=Decimal("0.00")))
        if self.total_net != net:
            raise ValueError(f"total_net {self.total_net} does not equal line sum {net}")

        by_rate: dict[Decimal, Decimal] = {}
        for line in self.lines:
            by_rate[line.vat_rate] = by_rate.get(line.vat_rate, Decimal("0.00")) + line.line_total
        vat = q2(sum((q2(base * rate) for rate, base in by_rate.items()), start=Decimal("0.00")))
        if self.total_vat != vat:
            raise ValueError(f"total_vat {self.total_vat} does not equal computed VAT {vat}")
        if self.total_gross != q2(net + vat):
            raise ValueError(f"total_gross {self.total_gross} does not equal net+VAT")
        return self
