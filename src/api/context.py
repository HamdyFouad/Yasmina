"""Assemble agent services for API requests."""

from __future__ import annotations

import logging
from decimal import Decimal

from src.agents.deepseek import DeepSeekClient
from src.agents.state import AgentContext, ItemSpecExtractor
from src.config import Settings
from src.services.approval_service import ApprovalService
from src.services.matching_service import HybridMatchingService
from src.services.pricing_service import PricingService
from src.services.unit_converter import UnitConverter

__all__ = ["build_agent_context"]

logger = logging.getLogger("yasmina.agents.context")


def build_agent_context(settings: Settings) -> AgentContext:
    """Build production services, using live DeepSeek only when configured.

    There is no mock LLM in the production path. Missing credentials/disabling
    ``DEEPSEEK_ENABLED`` simply leaves deterministic enrichment off; configured
    credentials instantiate the real OpenAI-compatible client.
    """

    llm: ItemSpecExtractor | None = None
    api_key = settings.deepseek_api_key
    has_key = api_key is not None and bool(api_key.get_secret_value().strip())
    if settings.deepseek_enabled and has_key:
        try:
            llm = DeepSeekClient(settings)
        except Exception as exc:  # unavailable SDK, invalid endpoint/model, etc.
            logger.warning("DeepSeek configured but unavailable; continuing without LLM: %s", exc)

    return AgentContext(
        approval=ApprovalService(settings.ed25519_approval_public_key),
        pricing=PricingService(
            default_vat_rate=Decimal(str(settings.default_vat_rate)),
            default_margin_rate=Decimal(str(settings.default_margin_rate)),
        ),
        matcher=HybridMatchingService(),
        converter=UnitConverter(),
        llm=llm,
    )
