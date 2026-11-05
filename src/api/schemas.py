"""Request/response DTOs for the FastAPI business endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from src.agents.state import OfferRequest
from src.models.schemas import CustomerOffer, SupplierQuote

__all__ = [
    "ApprovalPayload",
    "InvoiceBuyerPayload",
    "InvoiceRequestPayload",
    "MatchPayload",
    "OfferRequestPayload",
    "PricePayload",
    "TokenRequest",
    "VerifyPayload",
    "WorkflowPayload",
    "parse_customer_offer",
    "parse_supplier_quotes",
]


class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class OfferRequestPayload(BaseModel):
    customer_name: str = Field(min_length=1, max_length=256)
    offer_number: str = Field(min_length=1, max_length=128)
    valid_until: date
    margin_rate: Decimal = Field(default=Decimal("0.15"), ge=0, le=5)
    vat_rate: Decimal = Field(default=Decimal("0.19"), ge=0, le=1)
    customer_contact_email: EmailStr | None = None

    def to_request(self) -> OfferRequest:
        return OfferRequest(
            customer_name=self.customer_name,
            offer_number=self.offer_number,
            valid_until=self.valid_until,
            margin_rate=self.margin_rate,
            vat_rate=self.vat_rate,
            customer_contact_email=str(self.customer_contact_email) if self.customer_contact_email else None,
        )


class MatchPayload(BaseModel):
    query_embedding: list[float] | None = None
    limit: int = Field(default=5, ge=1, le=50)


class PricePayload(BaseModel):
    supplier_quotes: list[dict[str, Any]] = Field(min_length=1)
    offer_request: OfferRequestPayload


class WorkflowPayload(PricePayload):
    offer: dict[str, Any] | None = None
    export_path: str | None = None


class InvoiceBuyerPayload(BaseModel):
    street: str = Field(min_length=1, max_length=256)
    city: str = Field(min_length=1, max_length=128)
    postal_code: str = Field(min_length=1, max_length=32)
    country_code: str = Field(default="DE", min_length=2, max_length=2)
    vat_id: str | None = Field(default=None, max_length=32)


class InvoiceRequestPayload(BaseModel):
    buyer: InvoiceBuyerPayload
    buyer_reference: str = Field(min_length=5, max_length=46)
    due_date: date
    delivery_date: date | None = None
    iban: str | None = Field(default=None, max_length=34)
    payment_terms: str | None = Field(default=None, max_length=512)


class ApprovalPayload(BaseModel):
    offer: dict[str, Any]
    signature: str = Field(min_length=128, max_length=128)
    approver: str = Field(min_length=1, max_length=256)
    approved_at: datetime | None = None
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
