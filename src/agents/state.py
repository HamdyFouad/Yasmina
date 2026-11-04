"""State and dependency context for the LangGraph tender workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, TypedDict

from src.models.schemas import CustomerOffer, SupplierQuote, Tender
from src.services.approval_service import ApprovalService
from src.services.matching_service import CatalogCandidate, HybridMatchingService, MatchResult
from src.services.pricing_service import PricingService

__all__ = [
    "AgentContext",
    "AgentState",
    "AgentStatus",
    "ItemSpecExtractor",
    "OfferRequest",
    "initial_state",
]


class ItemSpecExtractor(Protocol):
    """Minimal protocol for an LLM-backed item-spec extractor."""

    def extract_item_specs(self, text: str) -> Any: ...


class AgentStatus:
    """Status values used by the graph (plain constants keep state serializable)."""

    NEW = "new"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXPORTED = "exported"
    APPROVAL_INVALID = "approval_invalid"
    FAILED = "failed"


class AgentState(TypedDict, total=False):
    tender: Tender
    supplier_quotes: list[SupplierQuote]
    catalog: list[CatalogCandidate]
    offer_request: "OfferRequest"
    matches: dict[str, list[MatchResult]]
    offer: CustomerOffer | None
    status: str
    errors: list[str]
    warnings: list[str]
    gaeb_document: Any
    export_path: str | None
    exported_path: str | None


@dataclass(frozen=True, slots=True)
class OfferRequest:
    """Customer/offer metadata supplied by the caller."""

    customer_name: str
    offer_number: str
    valid_until: date
    margin_rate: Decimal = Decimal("0.15")
    vat_rate: Decimal = Decimal("0.19")
    customer_contact_email: str | None = None


@dataclass(frozen=True)
class AgentContext:
    """Injected services; tests replace the LLM and approval key."""

    approval: ApprovalService
    pricing: PricingService = field(default_factory=PricingService)
    matcher: HybridMatchingService = field(default_factory=HybridMatchingService)
    llm: ItemSpecExtractor | None = None


def initial_state(
    *,
    tender: Tender,
    supplier_quotes: list[SupplierQuote],
    catalog: list[CatalogCandidate] | None = None,
    request: OfferRequest,
    offer: CustomerOffer | None = None,
    gaeb_document: Any = None,
    export_path: str | None = None,
) -> AgentState:
    state: AgentState = {
        "tender": tender,
        "supplier_quotes": supplier_quotes,
        "catalog": catalog or [],
        "offer_request": request,
        "status": AgentStatus.NEW,
        "errors": [],
        "warnings": [],
        "gaeb_document": gaeb_document,
        "export_path": export_path,
    }
    if offer is not None:
        state["offer"] = offer
    return state
