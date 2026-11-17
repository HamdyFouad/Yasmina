"""Request/response DTOs for the FastAPI business endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, EmailStr, Field, model_validator

from src.agents.state import OfferRequest
from src.api.security import Role
from src.models.enums import UnitOfMeasure
from src.models.schemas import CustomerOffer, SupplierQuote

__all__ = [
    "ApprovalPayload",
    "CatalogOverridePayload",
    "InvoiceBuyerPayload",
    "InvoiceCancelPayload",
    "InvoiceCreditPayload",
    "InvoiceEmailPayload",
    "InvoicePaymentPayload",
    "InvoiceRequestPayload",
    "LogoutRequest",
    "ManualSupplierQuoteLinePayload",
    "ManualSupplierQuotePayload",
    "MatchPayload",
    "MfaCodePayload",
    "MfaDisablePayload",
    "OfferRequestPayload",
    "PasswordChangePayload",
    "PasswordResetPayload",
    "PasswordResetRequestPayload",
    "PricePayload",
    "RFQDispatchPayload",
    "RFQSupplier",
    "RefreshTokenRequest",
    "SupplierQuoteCallbackPayload",
    "TenderOwnerPayload",
    "TokenRequest",
    "UserCreatePayload",
    "UserRolesPayload",
    "VerifyPayload",
    "WorkflowPayload",
    "parse_customer_offer",
    "parse_supplier_quotes",
]


class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, min_length=6, max_length=16)
    recovery_code: str | None = Field(default=None, min_length=8, max_length=40)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class PasswordChangePayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class PasswordResetRequestPayload(BaseModel):
    username: str = Field(min_length=1, max_length=128)


class PasswordResetPayload(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    new_password: str = Field(min_length=1, max_length=256)


class MfaCodePayload(BaseModel):
    code: str = Field(min_length=6, max_length=16)


class MfaDisablePayload(MfaCodePayload):
    password: str = Field(min_length=1, max_length=256)


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    email: EmailStr | None = None
    roles: list[Role] = Field(min_length=1)


class UserRolesPayload(BaseModel):
    roles: list[Role] = Field(min_length=1, max_length=4)


class CatalogOverridePayload(BaseModel):
    """Pin, replace or clear manual merge overrides for one catalogue article."""

    supplier_name: str = Field(min_length=1, max_length=256)
    article_number: str = Field(min_length=1, max_length=128)
    fields: list[str] = Field(default_factory=list, max_length=32)
    mode: Literal["add", "replace", "clear"] = "add"

    @model_validator(mode="after")
    def _validate_fields(self) -> CatalogOverridePayload:
        cleaned = [field.strip() for field in self.fields if field.strip()]
        if self.mode != "clear" and not cleaned:
            raise ValueError("fields must contain at least one catalogue field")
        self.fields = list(dict.fromkeys(cleaned))
        return self


class OfferRequestPayload(BaseModel):
    customer_name: str = Field(min_length=1, max_length=256)
    offer_number: str = Field(min_length=1, max_length=128)
    valid_until: date
    margin_rate: Decimal = Field(default=Decimal("0.15"), ge=0, le=5, max_digits=6, decimal_places=4)
    vat_rate: Decimal = Field(default=Decimal("0.19"), ge=0, le=1, max_digits=6, decimal_places=4)
    customer_contact_email: EmailStr | None = None
    freight_flat_net: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=18, decimal_places=2)
    pallet_unit_net: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=18, decimal_places=2)
    pallet_count: int = Field(default=0, ge=0, le=10_000)

    def to_request(self) -> OfferRequest:
        return OfferRequest(
            customer_name=self.customer_name,
            offer_number=self.offer_number,
            valid_until=self.valid_until,
            margin_rate=self.margin_rate,
            vat_rate=self.vat_rate,
            customer_contact_email=str(self.customer_contact_email) if self.customer_contact_email else None,
            freight_flat_net=self.freight_flat_net,
            pallet_unit_net=self.pallet_unit_net,
            pallet_count=self.pallet_count,
        )


class TenderOwnerPayload(BaseModel):
    """Admin-only reassignment of a tender to another active user."""

    owner: str = Field(min_length=1, max_length=128)


class MatchPayload(BaseModel):
    query_embedding: list[float] | None = None
    limit: int = Field(default=5, ge=1, le=50)


class PricePayload(BaseModel):
    supplier_quotes: list[dict[str, Any]] = Field(min_length=1)
    offer_request: OfferRequestPayload


class WorkflowPayload(PricePayload):
    offer: dict[str, Any] | None = None
    export_path: str | None = None


class RFQSupplier(BaseModel):
    """One supplier contact targeted by an RFQ dispatch."""

    supplier_id: UUID | None = None
    name: str = Field(min_length=1, max_length=256)
    email: EmailStr
    contact_name: str | None = Field(default=None, max_length=256)


class RFQDispatchPayload(BaseModel):
    tender_id: UUID
    suppliers: list[RFQSupplier] = Field(min_length=1, max_length=200)
    response_deadline: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=4000)


class SupplierQuoteCallbackPayload(BaseModel):
    """n8n callback envelope containing one or more canonical supplier quotes."""

    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    quotes: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class ManualSupplierQuoteLinePayload(BaseModel):
    line_number: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=512)
    tender_item_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit: UnitOfMeasure
    unit_price_net: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    moq: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4)
    pack_size: int | None = Field(default=None, gt=0, le=1_000_000)
    delivery_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=4000)


class ManualSupplierQuotePayload(BaseModel):
    """Dashboard-created supplier quote bound to a tender."""

    tender_id: UUID
    supplier_name: str = Field(min_length=1, max_length=256)
    supplier_contact_email: EmailStr | None = None
    quote_reference: str = Field(min_length=1, max_length=128)
    valid_until: date | None = None
    payment_terms: str | None = Field(default=None, max_length=256)
    incoterms: str | None = Field(default=None, max_length=32)
    delivery_days: int | None = Field(default=None, ge=0, le=365)
    lines: list[ManualSupplierQuoteLinePayload] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_line_numbers(self) -> ManualSupplierQuotePayload:
        numbers = [line.line_number for line in self.lines]
        if len(numbers) != len(set(numbers)):
            raise ValueError("line_number values must be unique")
        return self


class InvoiceBuyerPayload(BaseModel):
    street: str = Field(min_length=1, max_length=256)
    city: str = Field(min_length=1, max_length=128)
    postal_code: str = Field(min_length=1, max_length=32)
    country_code: str = Field(default="DE", min_length=2, max_length=2)
    vat_id: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None


class InvoiceRequestPayload(BaseModel):
    buyer: InvoiceBuyerPayload
    buyer_reference: str = Field(min_length=5, max_length=46)
    due_date: date
    delivery_date: date | None = None
    iban: str | None = Field(default=None, max_length=34)
    payment_terms: str | None = Field(default=None, max_length=512)


class InvoicePaymentPayload(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)
    reference: str | None = Field(default=None, max_length=128)


class InvoiceCreditPayload(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    reason: str = Field(min_length=3, max_length=4000)
    issue_date: date | None = None


class InvoiceCancelPayload(BaseModel):
    reason: str = Field(min_length=3, max_length=512)


class InvoiceEmailPayload(BaseModel):
    recipient: EmailStr | None = None


class ApprovalPayload(BaseModel):
    """Unsigned offer submitted for server-side Ed25519 approval.

    The API signs on behalf of the authenticated approver with the custody key,
    so neither a client signature nor an approver name is accepted here.
    """

    offer: dict[str, Any]
    approved_at: datetime | None = None
    owner: str | None = Field(default=None, min_length=1, max_length=128)
    invoice: InvoiceRequestPayload | None = None


class VerifyPayload(BaseModel):
    offer: dict[str, Any]


def parse_supplier_quotes(raw: list[dict[str, Any]]) -> list[SupplierQuote]:
    quotes: list[SupplierQuote] = []
    for item in raw:
        quotes.append(SupplierQuote.model_validate_json(json.dumps(item, default=str)))
    return quotes


def parse_customer_offer(raw: dict[str, Any]) -> CustomerOffer:
    return CustomerOffer.model_validate_json(json.dumps(raw, default=str))
