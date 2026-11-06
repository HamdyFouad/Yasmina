"""Strict Pydantic v2 domain schemas for Project Yasmina.

Every model uses ``ConfigDict(strict=True, extra="forbid")``. That means raw
extraction output must pass through :mod:`src.parsers.normalizers` first: a
``str`` such as ``"19%"``, ``"Gr. 8"`` or ``"Karton"`` is rejected by design.

Note: these are *domain* models, not HTTP DTOs. API endpoints should translate
JSON primitives into ``Decimal``/``datetime``/``UUID``/enum instances (or use a
non-strict request DTO) before constructing them.

Field sets follow Section 4 of ``docs/DETAILED_SPEC.md`` plus the domain rules
in ``AGENTS.md``. Sections 5-8 of the spec are not yet published; fields marked
in ``docs/ASSUMPTIONS.md`` are the inferred contract.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from src.models.enums import (
    Currency,
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

__all__ = [
    "CustomerOffer",
    "CustomerOfferLine",
    "PriceTier",
    "SupplierQuote",
    "SupplierQuoteLine",
    "Tender",
    "TenderItem",
    "YasminaSchema",
    "approval_required",
    "q2",
    "q4",
]

# ---------------------------------------------------------------------------
# Numeric aliases & rounding helpers
# ---------------------------------------------------------------------------

Q2 = Decimal("0.01")
Q4 = Decimal("0.0001")

HEX64_PATTERN = r"^[0-9a-f]{64}$"
HEX128_PATTERN = r"^[0-9a-f]{128}$"

Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
Money2 = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
VatRate = Annotated[Decimal, Field(ge=0, le=1, max_digits=6, decimal_places=6)]
MarginRate = Annotated[Decimal, Field(ge=0, le=5, max_digits=9, decimal_places=6)]

ShortText = Annotated[str, Field(min_length=1, max_length=512)]
Reference = Annotated[str, Field(min_length=1, max_length=128)]
CpvCode = Annotated[str, Field(pattern=r"^\d{8}(-\d)?$")]
Gtin = Annotated[str, Field(pattern=r"^(?:\d{8}|\d{12,14})$")]
Ed25519Signature = Annotated[str, Field(pattern=HEX128_PATTERN, max_length=128)]
Sha256Hash = Annotated[str, Field(pattern=HEX64_PATTERN, max_length=64)]

APPROVAL_REQUIRED_STATUSES = frozenset(
    {
        CustomerOfferStatus.APPROVED,
        CustomerOfferStatus.SENT,
        CustomerOfferStatus.ACCEPTED,
    }
)


def q2(value: Decimal) -> Decimal:
    """Round to cents, half-up (deterministic, never banker's rounding)."""

    return value.quantize(Q2, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    """Round to four decimal places, half-up."""

    return value.quantize(Q4, rounding=ROUND_HALF_UP)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def approval_required(status: CustomerOfferStatus) -> bool:
    """Whether an offer in ``status`` must carry a valid Ed25519 approval."""

    return status in APPROVAL_REQUIRED_STATUSES


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class YasminaSchema(BaseModel):
    """Strict base model: no coercion, no unknown fields, no silent defaults."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        from_attributes=True,
    )


# ---------------------------------------------------------------------------
# Tender (Ausschreibung)
# ---------------------------------------------------------------------------


class TenderItem(YasminaSchema):
    """A single tendered position (GAEB ``Position`` / PDF table row)."""

    id: UUID = Field(default_factory=uuid4)
    tender_id: UUID | None = None

    position_number: Annotated[str, Field(min_length=1, max_length=64)]
    lot: Annotated[str, Field(max_length=64)] | None = None

    short_text: Annotated[str, Field(min_length=1, max_length=512)]
    long_text: Annotated[str, Field(max_length=8000)] | None = None

    quantity: Quantity
    unit: UnitOfMeasure

    glove_size: GloveSize = GloveSize.NOT_APPLICABLE
    material: MaterialType | None = None
    color: StandardizedColor | None = None

    thickness_um: Annotated[Decimal, Field(ge=1, le=2000, decimal_places=2)] | None = None
    dimensions: Annotated[str, Field(max_length=128)] | None = None
    pack_size: Annotated[int, Field(gt=0, le=1_000_000)] | None = None
    packs_per_carton: Annotated[int, Field(gt=0, le=1_000_000)] | None = None
    packaging_description: Annotated[str, Field(max_length=256)] | None = None

    delivery_location: Annotated[str, Field(max_length=256)] | None = None
    requested_delivery_date: date | None = None

    cpv_code: CpvCode | None = None
    is_alternative_allowed: bool = False

    notes: Annotated[str, Field(max_length=4000)] | None = None
    source_reference: Annotated[str, Field(max_length=256)] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Tender(YasminaSchema):
    """A public or private tender with its normalized positions."""

    id: UUID = Field(default_factory=uuid4)
    tender_number: Annotated[str, Field(min_length=1, max_length=64)]
    title: Annotated[str, Field(min_length=1, max_length=512)]
    description: Annotated[str, Field(max_length=8000)] | None = None

    client_name: Annotated[str, Field(min_length=1, max_length=256)]
    client_contact_email: EmailStr | None = None

    procedure: ProcurementProcedure
    source_format: TenderSourceFormat
    source_reference: Annotated[str, Field(max_length=512)] | None = None
    owner: Annotated[str, Field(max_length=128)] | None = None
    cpv_codes: list[CpvCode] = Field(default_factory=list)

    publication_date: date | None = None
    submission_deadline: AwareDatetime

    currency: Currency = Currency.EUR
    status: TenderStatus = TenderStatus.DRAFT
    vat_rate: VatRate = Decimal("0.19")

    items: list[TenderItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    created_at: AwareDatetime = Field(default_factory=_utcnow)
    updated_at: AwareDatetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _validate_dates(self) -> Self:
        if self.publication_date and self.submission_deadline.date() < self.publication_date:
            raise ValueError("submission_deadline must not precede publication_date")
        return self

    @model_validator(mode="after")
    def _validate_unique_positions(self) -> Self:
        seen: set[tuple[str, str]] = set()
        for item in self.items:
            key = (item.lot or "", item.position_number)
            if key in seen:
                raise ValueError(f"duplicate position_number {item.position_number!r} in lot {item.lot!r}")
            seen.add(key)
        return self

    @field_validator("cpv_codes")
    @classmethod
    def _deduplicate_cpv_codes(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


# ---------------------------------------------------------------------------
# Supplier quotes (Lieferantenangebot)
# ---------------------------------------------------------------------------


class PriceTier(YasminaSchema):
    """A volume price break.

    ``min_quantity`` is inclusive: at or above this quantity,
    ``unit_price_net`` applies.
    """

    min_quantity: Quantity
    unit_price_net: Money


class SupplierQuoteLine(YasminaSchema):
    """One quoted article/position of a supplier quotation."""

    id: UUID = Field(default_factory=uuid4)
    supplier_quote_id: UUID | None = None
    line_number: Annotated[int, Field(ge=1)]

    description: Annotated[str, Field(min_length=1, max_length=512)]
    tender_item_id: UUID | None = None

    material: MaterialType | None = None
    color: StandardizedColor | None = None
    glove_size: GloveSize = GloveSize.NOT_APPLICABLE

    quantity: Quantity
    unit: UnitOfMeasure
    unit_price_net: Money
    moq: Quantity | None = None
    tiers: list[PriceTier] = Field(default_factory=list)

    pack_size: Annotated[int, Field(gt=0, le=1_000_000)] | None = None
    gtin: Gtin | None = None
    delivery_days: Annotated[int, Field(ge=0, le=365)] | None = None

    notes: Annotated[str, Field(max_length=4000)] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_tiers(self) -> Self:
        previous = Decimal(0)
        for tier in self.tiers:
            if tier.min_quantity <= previous:
                raise ValueError("tier min_quantity values must be strictly increasing")
            previous = tier.min_quantity
        return self


class SupplierQuote(YasminaSchema):
    """A supplier's quotation, either tender-bound or free-standing."""

    id: UUID = Field(default_factory=uuid4)
    supplier_id: UUID | None = None
    supplier_name: Annotated[str, Field(min_length=1, max_length=256)]
    supplier_contact_email: EmailStr | None = None

    quote_reference: Reference
    tender_id: UUID | None = None
    status: SupplierQuoteStatus = SupplierQuoteStatus.RECEIVED
    currency: Currency = Currency.EUR

    valid_until: date | None = None
    payment_terms: Annotated[str, Field(max_length=256)] | None = None
    incoterms: Annotated[str, Field(max_length=32)] | None = None
    delivery_days: Annotated[int, Field(ge=0, le=365)] | None = None

    lines: list[SupplierQuoteLine] = Field(min_length=1)
    total_net: Money2 | None = None

    source_document: Annotated[str, Field(max_length=512)] | None = None
    raw_extraction: dict[str, Any] = Field(default_factory=dict)

    created_at: AwareDatetime = Field(default_factory=_utcnow)
    updated_at: AwareDatetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _validate_line_ownership(self) -> Self:
        for line in self.lines:
            if line.supplier_quote_id is not None and line.supplier_quote_id != self.id:
                raise ValueError(
                    f"line {line.line_number} belongs to supplier quote {line.supplier_quote_id}, "
                    f"not {self.id}"
                )
        return self

    @model_validator(mode="after")
    def _validate_unique_line_numbers(self) -> Self:
        numbers = [line.line_number for line in self.lines]
        if len(numbers) != len(set(numbers)):
            raise ValueError("line_number values must be unique within a supplier quote")
        return self


# ---------------------------------------------------------------------------
# Customer offers (Kundenangebot)
# ---------------------------------------------------------------------------


class CustomerOfferLine(YasminaSchema):
    """One calculated offer line.

    Selling price and line total are derived from the deterministic pricing
    rules, so the schema refuses inconsistent values instead of silently
    recomputing them.
    """

    id: UUID = Field(default_factory=uuid4)
    offer_id: UUID | None = None

    position_number: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str, Field(min_length=1, max_length=512)]

    tender_item_id: UUID | None = None
    supplier_quote_line_id: UUID | None = None

    quantity: Quantity
    unit: UnitOfMeasure

    cost_unit_price_net: Money
    margin_rate: MarginRate
    unit_price_net: Money
    total_net: Money2
    vat_rate: VatRate = Decimal("0.19")

    @model_validator(mode="after")
    def _validate_selling_price(self) -> Self:
        expected_unit = q4(self.cost_unit_price_net * (Decimal(1) + self.margin_rate))
        if self.unit_price_net != expected_unit:
            raise ValueError(
                f"unit_price_net {self.unit_price_net} does not equal "
                f"cost*(1+margin) = {expected_unit}"
            )
        expected_total = q2(self.quantity * self.unit_price_net)
        if self.total_net != expected_total:
            raise ValueError(f"total_net {self.total_net} does not equal quantity*unit_price = {expected_total}")
        return self


class CustomerOffer(YasminaSchema):
    """An outbound customer offer guarded by Ed25519 human approval.

    An offer may only reach ``APPROVED``/``SENT``/``ACCEPTED`` — or carry an
    ERPNext quotation reference / GAEB export path — when the Ed25519 signature,
    the signed payload hash, the approver and the approval timestamp are present.
    """

    id: UUID = Field(default_factory=uuid4)
    offer_number: Reference
    customer_name: Annotated[str, Field(min_length=1, max_length=256)]
    customer_contact_email: EmailStr | None = None
    customer_reference: Annotated[str, Field(max_length=128)] | None = None
    tender_id: UUID | None = None

    status: CustomerOfferStatus = CustomerOfferStatus.DRAFT
    currency: Currency = Currency.EUR

    lines: list[CustomerOfferLine] = Field(min_length=1)
    margin_rate: MarginRate
    total_net: Money2
    vat_rate: VatRate = Decimal("0.19")
    total_vat: Money2
    total_gross: Money2

    valid_until: date
    payment_terms: Annotated[str, Field(max_length=256)] | None = None
    delivery_terms: Annotated[str, Field(max_length=256)] | None = None

    ed25519_approval_signature: Ed25519Signature | None = None
    approval_payload_hash: Sha256Hash | None = None
    approved_by: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    approved_at: AwareDatetime | None = None

    owner: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    erpnext_quotation_name: Annotated[str, Field(max_length=140)] | None = None
    invoice_artifacts: dict[str, Any] = Field(default_factory=dict)
    gaeb_export_path: Annotated[str, Field(max_length=512)] | None = None

    notes: Annotated[str, Field(max_length=8000)] | None = None
    created_at: AwareDatetime = Field(default_factory=_utcnow)
    updated_at: AwareDatetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _validate_line_ownership(self) -> Self:
        for line in self.lines:
            if line.offer_id is not None and line.offer_id != self.id:
                raise ValueError(f"line {line.position_number} belongs to offer {line.offer_id}, not {self.id}")
        return self

    @model_validator(mode="after")
    def _validate_unique_positions(self) -> Self:
        positions = [line.position_number for line in self.lines]
        if len(positions) != len(set(positions)):
            raise ValueError("position_number values must be unique within a customer offer")
        return self

    @model_validator(mode="after")
    def _validate_totals(self) -> Self:
        line_sum = sum((line.total_net for line in self.lines), start=Decimal("0.00"))
        if q2(line_sum) != self.total_net:
            raise ValueError(f"total_net {self.total_net} does not equal sum of lines {q2(line_sum)}")

        expected_vat = q2(self.total_net * self.vat_rate)
        by_rate: dict[Decimal, Decimal] = {}
        for line in self.lines:
            by_rate[line.vat_rate] = by_rate.get(line.vat_rate, Decimal("0.00")) + line.total_net
        expected_vat_by_line = q2(
            sum((q2(base * rate) for rate, base in by_rate.items()), start=Decimal("0.00"))
        )
        if self.total_vat != expected_vat_by_line:
            raise ValueError(
                f"total_vat {self.total_vat} does not equal the line-rate split "
                f"{expected_vat_by_line} (global rate would be {expected_vat})"
            )

        expected_gross = q2(self.total_net + self.total_vat)
        if self.total_gross != expected_gross:
            raise ValueError(f"total_gross {self.total_gross} does not equal net+vat = {expected_gross}")
        return self

    @model_validator(mode="after")
    def _validate_hitl_approval(self) -> Self:
        exports_approval = self.erpnext_quotation_name is not None or self.gaeb_export_path is not None
        if approval_required(self.status) or exports_approval:
            missing = [
                field
                for field, value in {
                    "ed25519_approval_signature": self.ed25519_approval_signature,
                    "approval_payload_hash": self.approval_payload_hash,
                    "approved_by": self.approved_by,
                    "approved_at": self.approved_at,
                }.items()
                if value is None
            ]
            if missing:
                raise ValueError(
                    "HITL approval required (Ed25519 signature) before export/approval; missing: "
                    + ", ".join(missing)
                )
        return self
