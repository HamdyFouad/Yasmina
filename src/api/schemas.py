"""Request/response DTOs for the FastAPI business endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from src.agents.state import OfferRequest
from src.api.security import Role
from src.models.enums import PurchaseOrderStatus, UnitOfMeasure
from src.models.schemas import CustomerOffer, SupplierQuote

__all__ = [
    "ApprovalPayload",
    "CatalogOverridePayload",
    "CustomerPayload",
    "InvoiceBuyerPayload",
    "InvoiceCancelPayload",
    "InvoiceCreditPayload",
    "InvoiceEmailPayload",
    "InvoicePaymentPayload",
    "InvoiceRequestPayload",
    "IngestionDiscardPayload",
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
    "PurchaseOrderCreatePayload",
    "PurchaseOrderStatusPayload",
    "RFQDispatchPayload",
    "RFQSupplier",
    "RefreshTokenRequest",
    "ReleaseStaleLocksPayload",
    "SupplierInvoiceLinePayload",
    "SupplierInvoicePayload",
    "SupplierQuoteCallbackPayload",
    "SupplierContactPayload",
    "SupplierPayload",
    "TenderOwnerPayload",
    "TokenRequest",
    "UserCreatePayload",
    "WorkerCommandCancelPayload",
    "WorkerIngestionTriggerPayload",
    "WorkerScrapeTriggerPayload",
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


class IngestionDiscardPayload(BaseModel):
    """Admin reason recorded when a failed ingestion job is discarded."""

    reason: str = Field(default="discarded by operator", min_length=3, max_length=512)


class ReleaseStaleLocksPayload(BaseModel):
    """Age threshold for resetting ingestion jobs stuck in ``processing``."""

    older_than_seconds: int = Field(default=900, ge=60, le=86_400)


class WorkerCommandCancelPayload(BaseModel):
    """Operator reason recorded when a running worker command is cancelled."""

    reason: str = Field(default="cancelled by operator", min_length=3, max_length=512)


class SupplierContactPayload(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=256)
    role: str | None = Field(default=None, max_length=128)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    is_primary: bool = False


class SupplierPayload(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    legal_name: str | None = Field(default=None, max_length=256)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    street: str | None = Field(default=None, max_length=256)
    postal_code: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=128)
    country_code: str = Field(default="DE", min_length=2, max_length=2)
    payment_terms: str | None = Field(default=None, max_length=512)
    delivery_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=4000)
    is_active: bool = True
    contacts: list[SupplierContactPayload] = Field(default_factory=list)

    @field_validator("country_code")
    @classmethod
    def _upper_country(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _single_primary_contact(self) -> SupplierPayload:
        if sum(1 for contact in self.contacts if contact.is_primary) > 1:
            raise ValueError("only one supplier contact can be primary")
        if self.contacts and not any(contact.is_primary for contact in self.contacts):
            self.contacts[0].is_primary = True
        return self


class CustomerPayload(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    legal_name: str | None = Field(default=None, max_length=256)
    street: str = Field(min_length=1, max_length=256)
    postal_code: str = Field(min_length=1, max_length=32)
    city: str = Field(min_length=1, max_length=128)
    country_code: str = Field(default="DE", min_length=2, max_length=2)
    vat_id: str | None = Field(default=None, max_length=32)
    leitweg_id: str | None = Field(default=None, max_length=46)
    payment_terms: str | None = Field(default=None, max_length=512)
    contact_name: str | None = Field(default=None, max_length=256)
    contact_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    is_active: bool = True

    @field_validator("country_code")
    @classmethod
    def _upper_country(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _validate_leitweg(self) -> CustomerPayload:
        if self.leitweg_id is not None:
            reference = self.leitweg_id.strip()
            if reference and not 5 <= len(reference) <= 46:
                raise ValueError("the Leitweg-ID must be 5 to 46 characters")
            self.leitweg_id = reference or None
        return self


class WorkerScrapeTriggerPayload(BaseModel):
    """Admin request to launch one scrape worker process."""

    portal: str = Field(default="all", min_length=1, max_length=64)
    max_pages: int | None = Field(default=1, ge=1, le=5)


class WorkerIngestionTriggerPayload(BaseModel):
    """Admin request to drain a bounded batch of ingestion jobs."""

    limit: int = Field(default=10, ge=1, le=50)


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


class PurchaseOrderCreatePayload(BaseModel):
    """Generate a PO from one accepted supplier quotation."""

    supplier_quote_id: UUID
    customer_offer_id: UUID | None = None
    expected_delivery_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class PurchaseOrderStatusPayload(BaseModel):
    """Move a PO through the procurement state machine."""

    status: PurchaseOrderStatus


class SupplierInvoiceLinePayload(BaseModel):
    purchase_order_line_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_price_net: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    description: str | None = Field(default=None, max_length=512)


class SupplierInvoicePayload(BaseModel):
    """Incoming supplier invoice captured against a purchase order."""

    purchase_order_id: UUID
    invoice_number: str = Field(min_length=1, max_length=128)
    invoice_date: date
    delivery_date: date | None = None
    due_date: date | None = None
    total_net: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    vat_rate: Decimal = Field(default=Decimal("0.1900"), ge=0, le=1, max_digits=6, decimal_places=4)
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[SupplierInvoiceLinePayload] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _validate_lines_and_dates(self) -> SupplierInvoicePayload:
        line_ids = [line.purchase_order_line_id for line in self.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("each purchase-order line may appear only once")
        if self.due_date is not None and self.due_date < self.invoice_date:
            raise ValueError("due_date must not precede invoice_date")
        if self.delivery_date is not None and self.delivery_date < self.invoice_date:
            raise ValueError("delivery_date must not precede invoice_date")
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
