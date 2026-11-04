"""Assemble agent services for API requests."""

from __future__ import annotations

from decimal import Decimal

from src.agents.state import AgentContext
from src.config import Settings
from src.services.approval_service import ApprovalService
from src.services.matching_service import HybridMatchingService
from src.services.pricing_service import PricingService

__all__ = ["build_agent_context"]


def build_agent_context(settings: Settings) -> AgentContext:
    return AgentContext(
        approval=ApprovalService(settings.ed25519_approval_public_key),
        pricing=PricingService(
            default_vat_rate=Decimal(str(settings.default_vat_rate)),
            default_margin_rate=Decimal(str(settings.default_margin_rate)),
        ),
        matcher=HybridMatchingService(),
        llm=None,  # LLM enrichment is opt-in; API requests stay deterministic by default.
    )
