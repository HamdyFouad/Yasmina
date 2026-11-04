"""Deterministic domain services (pricing, matching and approval)."""

from __future__ import annotations

from src.services.approval_service import (
    ApprovalError,
    ApprovalService,
    approval_payload,
    approval_payload_hash,
    approve_offer,
    generate_keypair,
    sign_offer,
    verify_offer_signature,
)
from src.services.matching_service import (
    CatalogCandidate,
    HybridMatchingService,
    MatchResult,
    MatchingError,
    MatchingService,
    candidate_from_orm,
    cosine_similarity,
    lexical_similarity,
)
from src.services.pricing_service import (
    OfferPricing,
    PricedLine,
    PricingError,
    PricingService,
    calculate_unit_price,
    round_quantity,
    select_tier_price,
)

__all__ = [
    "ApprovalError",
    "ApprovalService",
    "CatalogCandidate",
    "HybridMatchingService",
    "MatchResult",
    "MatchingError",
    "MatchingService",
    "OfferPricing",
    "PricedLine",
    "PricingError",
    "PricingService",
    "approval_payload",
    "approval_payload_hash",
    "approve_offer",
    "calculate_unit_price",
    "candidate_from_orm",
    "cosine_similarity",
    "generate_keypair",
    "lexical_similarity",
    "round_quantity",
    "select_tier_price",
    "sign_offer",
    "verify_offer_signature",
]
