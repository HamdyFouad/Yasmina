"""LangGraph workflow and DeepSeek configuration tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.agents.deepseek import DeepSeekClient, DeepSeekUnavailable, RawItemSpecs
from src.agents.graph import run_agent
from src.agents.nodes import approval_gate, export_offer, route_after_approval
from src.agents.state import AgentContext, AgentStatus, OfferRequest, initial_state
from src.config import Settings
from src.models.enums import (
    CustomerOfferStatus,
    GloveSize,
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    TenderSourceFormat,
    UnitOfMeasure,
)
from src.models.schemas import (
    SupplierQuote,
    SupplierQuoteLine,
    Tender,
    TenderItem,
)
from src.services.approval_service import ApprovalService, generate_keypair, sign_offer
from src.services.matching_service import CatalogCandidate

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


def make_item(**overrides: object) -> TenderItem:
    data: dict[str, object] = {
        "position_number": "1.1",
        "short_text": "Nitrilhandschuhe blau Gr. M",
        "quantity": Decimal("10.0000"),
        "unit": UnitOfMeasure.KARTON,
        "material": MaterialType.NITRILE,
        "color": StandardizedColor.BLUE,
        "glove_size": GloveSize.M,
    }
    data.update(overrides)
    return TenderItem(**data)


def make_tender(item: TenderItem | None = None) -> Tender:
    return Tender(
        tender_number="T-2026-001",
        title="Rahmenvertrag Hygiene",
        client_name="Stadt Muster",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.MANUAL,
        submission_deadline=NOW + timedelta(days=14),
        items=[item or make_item()],
    )


def make_quote(item: TenderItem) -> SupplierQuote:
    line = SupplierQuoteLine(
        line_number=1,
        description=item.short_text,
        tender_item_id=item.id,
        quantity=item.quantity,
        unit=item.unit,
        unit_price_net=Decimal("10.0000"),
    )
    return SupplierQuote(
        supplier_name="Hygiene Gross GmbH",
        quote_reference="ANG-77",
        lines=[line],
    )


def make_request() -> OfferRequest:
    return OfferRequest(
        customer_name="Stadt Muster",
        offer_number="AN-2026-0001",
        valid_until=date(2026, 12, 31),
        margin_rate=Decimal("0.15"),
        vat_rate=Decimal("0.19"),
    )


def test_graph_prices_and_stops_for_human_approval() -> None:
    item = make_item()
    tender = make_tender(item)
    quote = make_quote(item)
    _, public_hex = generate_keypair()
    context = AgentContext(approval=ApprovalService(public_hex))

    result = run_agent(
        initial_state(
            tender=tender,
            supplier_quotes=[quote],
            catalog=[
                CatalogCandidate(
                    id="c1",
                    article_number="ART-1",
                    description="Nitril Einweghandschuhe blau Größe M",
                    material=MaterialType.NITRILE,
                    color=StandardizedColor.BLUE,
                    glove_size=GloveSize.M,
                    unit=UnitOfMeasure.KARTON,
                )
            ],
            request=make_request(),
        ),
        context,
    )

    assert result["status"] == AgentStatus.PENDING_APPROVAL
    assert result["offer"] is not None
    assert result["offer"].status is CustomerOfferStatus.DRAFT
    assert result["offer"].total_net == Decimal("115.00")
    assert result["offer"].total_gross == Decimal("136.85")
    assert result["matches"]


def test_resumed_graph_exports_approved_offer() -> None:
    item = make_item()
    tender = make_tender(item)
    quote = make_quote(item)
    private_hex, public_hex = generate_keypair()
    context = AgentContext(approval=ApprovalService(public_hex))

    first = run_agent(
        initial_state(tender=tender, supplier_quotes=[quote], request=make_request()),
        context,
    )
    draft = first["offer"]
    assert draft is not None

    approved = sign_offer(
        draft,
        private_hex,
        approver="C. Mueller",
        approved_at=NOW,
    )
    resumed = run_agent(
        initial_state(
            tender=tender,
            supplier_quotes=[quote],
            request=make_request(),
            offer=approved,
        ),
        context,
    )

    assert resumed["status"] == AgentStatus.APPROVED
    assert resumed["offer"] is not None
    assert resumed["offer"].status is CustomerOfferStatus.APPROVED


def test_invalid_approval_is_rejected_by_gate() -> None:
    item = make_item()
    tender = make_tender(item)
    quote = make_quote(item)
    _, public_hex = generate_keypair()
    context = AgentContext(approval=ApprovalService(public_hex))

    result = run_agent(
        initial_state(tender=tender, supplier_quotes=[quote], request=make_request()),
        context,
    )
    offer = result["offer"]
    assert offer is not None
    offer = offer.model_copy(
        update={
            "status": CustomerOfferStatus.APPROVED,
            "ed25519_approval_signature": "ab" * 64,
            "approval_payload_hash": "cd" * 32,
            "approved_by": "Fake",
            "approved_at": NOW,
        }
    )
    result["offer"] = offer

    assert approval_gate(result, context)["status"] == AgentStatus.APPROVAL_INVALID
    assert export_offer(result, context)["status"] == AgentStatus.APPROVAL_INVALID
    assert route_after_approval({"status": AgentStatus.APPROVAL_INVALID}) == "stop"


def test_missing_quote_fails_pricing() -> None:
    tender = make_tender()
    _, public_hex = generate_keypair()
    context = AgentContext(approval=ApprovalService(public_hex))

    result = run_agent(
        initial_state(tender=tender, supplier_quotes=[], request=make_request()),
        context,
    )

    assert result["status"] == AgentStatus.FAILED
    assert result.get("offer") is None
    assert any("no supplier quote" in error for error in result["errors"])


def test_llm_enrichment_fills_missing_specs() -> None:
    item = make_item(material=None, color=None, glove_size=GloveSize.NOT_APPLICABLE)
    tender = make_tender(item)
    quote = make_quote(item)
    _, public_hex = generate_keypair()

    class FakeLLM:
        def extract_item_specs(self, text: str) -> RawItemSpecs:
            return RawItemSpecs(
                material="Nitril",
                color="blau",
                glove_size="8",
                thickness="120 µm",
                pack_size="1000 Stück",
            )

    context = AgentContext(approval=ApprovalService(public_hex), llm=FakeLLM())

    result = run_agent(
        initial_state(tender=tender, supplier_quotes=[quote], request=make_request()),
        context,
    )

    enriched = result["tender"].items[0]
    assert enriched.material is MaterialType.NITRILE
    assert enriched.color is StandardizedColor.BLUE
    assert enriched.glove_size is GloveSize.M
    assert enriched.thickness_um == Decimal("120.00")
    assert enriched.pack_size == 1000


def test_deepseek_client_requires_api_key() -> None:
    settings = Settings(_env_file=None, database_url=None)

    with pytest.raises(DeepSeekUnavailable, match="DEEPSEEK_API_KEY"):
        DeepSeekClient(settings)
