"""Live PostgreSQL tests for offer persistence and LangGraph resumption.

Set ``YASMINA_TEST_DATABASE_URL`` (for example
``postgresql+asyncpg://yasmina:yasmina@localhost:5432/yasmina``) after applying
migrations to run these tests. The regular CI suite skips them without a
provisioned database.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.agents.checkpoint import SqlAlchemyCheckpointSaver
from src.agents.graph import run_agent_async
from src.agents.state import AgentContext, OfferRequest, initial_state
from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.models.enums import (
    CustomerOfferStatus,
    GloveSize,
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    TenderSourceFormat,
    UnitOfMeasure,
)
from src.models.orm import CustomerOffer as CustomerOfferORM
from src.models.schemas import (
    CustomerOffer,
    CustomerOfferLine,
    SupplierQuote,
    SupplierQuoteLine,
    Tender,
    TenderItem,
    q2,
    q4,
)
from src.repositories.offer_repository import OfferRepository
from src.services.approval_service import (
    ApprovalService,
    generate_keypair,
    sign_offer,
)

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="live-test-jwt-secret-with-at-least-32-bytes",
        api_admin_password="test-admin-password",
    )


def make_offer(*, offer_number: str) -> CustomerOffer:
    cost = Decimal("10.0000")
    margin = Decimal("0.15")
    unit_price = q4(cost * (Decimal(1) + margin))
    line = CustomerOfferLine(
        position_number="1.1",
        description="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
        cost_unit_price_net=cost,
        margin_rate=margin,
        unit_price_net=unit_price,
        total_net=q2(Decimal("10.0000") * unit_price),
    )
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    return CustomerOffer(
        offer_number=offer_number,
        customer_name="Stadt Muster",
        status=CustomerOfferStatus.DRAFT,
        lines=[line],
        margin_rate=margin,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
    )


async def test_offer_repository_roundtrip_and_idempotency() -> None:
    private_key, public_key = generate_keypair()
    settings_value = settings()
    factory = get_session_factory(settings_value)
    offer = sign_offer(
        make_offer(offer_number=f"DB-{uuid4().hex[:10]}"),
        private_key,
        approver="Tester",
        approved_at=NOW,
    )
    try:
        async with factory() as session:
            repository = OfferRepository(session)
            first = await repository.save(
                offer.model_copy(
                    update={
                        "owner": "sales-db",
                        "invoice_artifacts": {
                            "xrechnung": {"object_name": "offers/db/invoice.xml"}
                        },
                    }
                )
            )
            second = await repository.save(
                first.offer.model_copy(
                    update={
                        "invoice_artifacts": {
                            "zugferd": {"object_name": "offers/db/invoice.pdf"}
                        },
                        "erpnext_quotation_name": "QTN-DB-1",
                    }
                )
            )

            assert first.created is True
            assert second.created is False
            loaded = await repository.get(offer.id)
            assert loaded is not None
            assert loaded.offer.owner == "sales-db"
            assert loaded.offer.erpnext_quotation_name == "QTN-DB-1"
            assert loaded.offer.invoice_artifacts["xrechnung"]["object_name"].endswith(".xml")
            assert loaded.offer.invoice_artifacts["zugferd"]["object_name"].endswith(".pdf")
            assert [line.position_number for line in loaded.offer.lines] == ["1.1"]

            listed = await repository.list(owner="sales-db")
            assert any(item.offer_number == offer.offer_number for item in listed)

        async with factory() as session:
            await session.execute(delete(CustomerOfferORM).where(CustomerOfferORM.id == offer.id))
            await session.commit()
    finally:
        await dispose_engine()


async def test_langgraph_checkpoint_resumes_after_signing() -> None:
    private_key, public_key = generate_keypair()
    settings_value = settings()
    factory = get_session_factory(settings_value)
    checkpointer = SqlAlchemyCheckpointSaver(factory)
    thread_id = f"db-resume:{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    item = TenderItem(
        position_number="1.1",
        short_text="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
        material=MaterialType.NITRILE,
        color=StandardizedColor.BLUE,
        glove_size=GloveSize.M,
    )
    tender = Tender(
        tender_number=f"T-{uuid4().hex[:8]}",
        title="Rahmenvertrag Hygiene",
        client_name="Stadt Muster",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.MANUAL,
        submission_deadline=NOW,
        items=[item],
    )
    line = SupplierQuoteLine(
        line_number=1,
        description=item.short_text,
        tender_item_id=item.id,
        quantity=item.quantity,
        unit=item.unit,
        unit_price_net=Decimal("10.0000"),
    )
    quote = SupplierQuote(
        supplier_name="Hygiene Gross GmbH",
        quote_reference=f"Q-{uuid4().hex[:8]}",
        lines=[line],
    )
    request = OfferRequest(
        customer_name="Stadt Muster",
        offer_number=f"A-{uuid4().hex[:8]}",
        valid_until=date(2026, 12, 31),
        margin_rate=Decimal("0.15"),
        vat_rate=Decimal("0.19"),
    )
    context = AgentContext(approval=ApprovalService(public_key))

    try:
        first = await run_agent_async(
            initial_state(tender=tender, supplier_quotes=[quote], request=request),
            context,
            checkpointer=checkpointer,
            config=config,
        )
        assert first["status"] == "pending_approval"
        saved = await checkpointer.aget_tuple(config)
        assert saved is not None
        assert saved.checkpoint["channel_values"]["offer"] is not None

        signed = sign_offer(first["offer"], private_key, approver="Tester", approved_at=NOW)
        resumed = await run_agent_async(
            initial_state(
                tender=tender,
                supplier_quotes=[quote],
                request=request,
                offer=signed,
            ),
            context,
            checkpointer=checkpointer,
            config=config,
        )
        assert resumed["status"] == "approved"
        assert resumed["offer"].status is CustomerOfferStatus.APPROVED
        assert await checkpointer.aget_tuple(config) is not None
    finally:
        await checkpointer.adelete_thread(thread_id)
        await dispose_engine()
