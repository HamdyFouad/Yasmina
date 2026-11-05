"""API integration tests for ERPNext approval and n8n routes (mocked I/O)."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_service,
    get_erpnext_client,
    get_integration_repository,
    get_n8n_client,
    get_session,
    get_supplier_quote_repository,
    get_tender_repository,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.integrations.erpnext_client import ERPQuotation
from src.integrations.n8n_client import (
    N8nDispatchResult,
    sign_body,
    verify_callback_signature,
)
from src.main import create_app
from src.models.enums import (
    CustomerOfferStatus,
    ProcurementProcedure,
    TenderSourceFormat,
    UnitOfMeasure,
)
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
from src.repositories.integration_repository import IdempotencyRecord
from src.repositories.supplier_quote_repository import StoredSupplierQuote
from src.repositories.tender_repository import StoredTender
from src.services.approval_service import generate_keypair, sign_offer

PRIVATE_KEY, PUBLIC_KEY = generate_keypair()
JWT_SECRET = "test-jwt-secret-key-with-at-least-32-bytes"
N8N_SECRET = "test-n8n-callback-secret"
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        jwt_secret_key=JWT_SECRET,
        api_admin_password="test-admin-password",
        ed25519_approval_public_key=PUBLIC_KEY,
        n8n_webhook_hmac_secret=N8N_SECRET,
        n8n_callback_url="https://api.example.test/api/v1/integrations/n8n/supplier-quotes",
        n8n_callback_max_age_seconds=300,
    )


SETTINGS = make_settings()
SALES_TOKEN = create_access_token(SETTINGS, "sales-user", [Role.SALES])
APPROVER_TOKEN = create_access_token(SETTINGS, "approver-user", [Role.APPROVER])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_tender() -> Tender:
    return Tender(
        tender_number="T-1",
        title="Rahmenvertrag Hygiene",
        client_name="Stadt Muster",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.GAEB_X83,
        submission_deadline=datetime(2026, 2, 1, tzinfo=timezone.utc),
        items=[
            TenderItem(
                position_number="1.1",
                short_text="Nitrilhandschuhe blau Gr. M",
                quantity=Decimal("10.0000"),
                unit=UnitOfMeasure.KARTON,
            )
        ],
    )


def make_offer() -> CustomerOffer:
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
        offer_number="AN-2026-0001",
        customer_name="Stadt Muster",
        customer_contact_email="einkauf@example.com",
        status=CustomerOfferStatus.DRAFT,
        lines=[line],
        margin_rate=margin,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
    )


def make_quote() -> SupplierQuote:
    return SupplierQuote(
        supplier_name="Hygiene Gross GmbH",
        supplier_contact_email="sales@example.com",
        quote_reference="ANG-77",
        lines=[
            SupplierQuoteLine(
                line_number=1,
                description="Nitrilhandschuhe blau Gr. M",
                quantity=Decimal("10.0000"),
                unit=UnitOfMeasure.KARTON,
                unit_price_net=Decimal("10.0000"),
            )
        ],
    )


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        return None


class FakeTenderRepository:
    def __init__(self, tender: Tender) -> None:
        self.tender = tender

    async def get(self, tender_id: UUID) -> StoredTender | None:
        if tender_id == self.tender.id:
            return StoredTender(tender=self.tender)
        return None


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)

    async def record_clamav(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


class FakeIntegrationRepository:
    def __init__(self) -> None:
        self.records: dict[str, IdempotencyRecord] = {}

    async def reserve(
        self,
        *,
        key: str,
        operation: str,
        request_hash: str,
    ) -> tuple[IdempotencyRecord, bool]:
        if key in self.records:
            return self.records[key], False
        record = IdempotencyRecord(key, operation, request_hash, "pending", None)
        self.records[key] = record
        return record, True

    async def mark_succeeded(self, key: str, response: dict[str, Any]) -> None:
        self.records[key] = replace(self.records[key], status="succeeded", response=response)

    async def mark_failed(self, key: str, error: str) -> None:
        self.records[key] = replace(self.records[key], status="failed", response={"error": error})

    async def reopen(self, key: str) -> None:
        self.records[key] = replace(self.records[key], status="pending", response=None)


class FakeQuoteRepository:
    def __init__(self) -> None:
        self.saved: list[SupplierQuote] = []

    async def save(self, quote: SupplierQuote) -> StoredSupplierQuote:
        self.saved.append(quote)
        return StoredSupplierQuote(quote_id=quote.id, created=True)


class FakeN8n:
    configured = True
    callback_url = "https://api.example.test/api/v1/integrations/n8n/supplier-quotes"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls: list[dict[str, Any]] = []

    async def dispatch_rfq(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> N8nDispatchResult:
        self.calls.append({"payload": payload, "idempotency_key": idempotency_key})
        return N8nDispatchResult(202, idempotency_key, {"accepted": True})

    def verify_callback(self, body: bytes, headers: Any) -> int:
        signature = next(
            (value for key, value in headers.items() if key.lower() == "x-yasmina-signature"),
            "",
        )
        timestamp = next(
            (value for key, value in headers.items() if key.lower() == "x-yasmina-timestamp"),
            "",
        )
        return verify_callback_signature(
            N8N_SECRET,
            body,
            timestamp,
            signature,
            max_age_seconds=300,
        )


class FakeERPNext:
    configured = True

    def __init__(self) -> None:
        self.offers: list[CustomerOffer] = []

    async def create_quotation(self, offer: CustomerOffer) -> ERPQuotation:
        self.offers.append(offer)
        return ERPQuotation(
            name="QTN-2026-0001",
            title=f"Yasmina {offer.offer_number} | {offer.customer_name}",
            created=True,
            payload={},
        )


@pytest.fixture()
def api() -> Any:
    app = create_app(SETTINGS, run_startup_checks=False)
    session = FakeSession()
    tender_repository = FakeTenderRepository(make_tender())
    audit = FakeAudit()
    integrations = FakeIntegrationRepository()
    quote_repository = FakeQuoteRepository()
    n8n = FakeN8n(SETTINGS)
    erpnext = FakeERPNext()

    async def fake_session() -> Any:
        yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_tender_repository] = lambda: tender_repository
    app.dependency_overrides[get_audit_service] = lambda: audit
    app.dependency_overrides[get_integration_repository] = lambda: integrations
    app.dependency_overrides[get_supplier_quote_repository] = lambda: quote_repository
    app.dependency_overrides[get_n8n_client] = lambda: n8n
    app.dependency_overrides[get_erpnext_client] = lambda: erpnext

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            session=session,
            tender=tender_repository.tender,
            audit=audit,
            integrations=integrations,
            quotes=quote_repository,
            n8n=n8n,
            erpnext=erpnext,
        )


def _dispatch_headers(key: str = "rfq-key-0001") -> dict[str, str]:
    return {**auth(SALES_TOKEN), "Idempotency-Key": key}


def test_rfq_dispatch_is_idempotent_and_signed(api: Any) -> None:
    payload = {
        "tender_id": str(api.tender.id),
        "suppliers": [{"name": "Hygiene Gross GmbH", "email": "sales@example.com"}],
        "notes": "Please quote by Friday",
    }

    first = api.client.post(
        "/api/v1/integrations/rfq/dispatch",
        json=payload,
        headers=_dispatch_headers(),
    )
    second = api.client.post(
        "/api/v1/integrations/rfq/dispatch",
        json=payload,
        headers=_dispatch_headers(),
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "dispatched"
    assert first.json()["webhook_status"] == 202
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True
    assert len(api.n8n.calls) == 1
    assert api.n8n.calls[0]["payload"]["tender"]["items"][0]["position_number"] == "1.1"
    assert api.n8n.calls[0]["payload"]["suppliers"][0]["email"] == "sales@example.com"
    assert api.audit.entries[-1]["event_type"].value == "RFQ_DISPATCHED"
    assert api.integrations.records["rfq-key-0001"].status == "succeeded"


def test_rfq_dispatch_rejects_key_reuse_and_missing_key(api: Any) -> None:
    payload = {
        "tender_id": str(api.tender.id),
        "suppliers": [{"name": "Hygiene Gross GmbH", "email": "sales@example.com"}],
    }
    assert (
        api.client.post(
            "/api/v1/integrations/rfq/dispatch",
            json=payload,
            headers=_dispatch_headers(),
        ).status_code
        == 200
    )

    changed = {**payload, "notes": "different payload"}
    conflict = api.client.post(
        "/api/v1/integrations/rfq/dispatch",
        json=changed,
        headers=_dispatch_headers(),
    )
    assert conflict.status_code == 409
    assert "different payload" in conflict.text

    missing = api.client.post(
        "/api/v1/integrations/rfq/dispatch",
        json=payload,
        headers=auth(SALES_TOKEN),
    )
    assert missing.status_code == 400
    assert "Idempotency-Key" in missing.text


def _signed_callback(body: bytes, *, timestamp: int | None = None) -> dict[str, str]:
    stamp = int(time.time()) if timestamp is None else timestamp
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": "quote-key-0001",
        "X-Yasmina-Timestamp": str(stamp),
        "X-Yasmina-Signature": sign_body(N8N_SECRET, body, stamp),
    }


def test_supplier_quote_callback_requires_valid_signature(api: Any) -> None:
    body = json.dumps({"event_id": "evt-1", "quotes": [make_quote().model_dump(mode="json")]}).encode()
    headers = _signed_callback(body)
    headers["X-Yasmina-Signature"] = "sha256=" + "0" * 64

    rejected = api.client.post(
        "/api/v1/integrations/n8n/supplier-quotes",
        content=body,
        headers=headers,
    )

    assert rejected.status_code == 401
    assert "invalid" in rejected.text
    assert api.quotes.saved == []

    stale = _signed_callback(body, timestamp=int(time.time()) - 301)
    expired = api.client.post(
        "/api/v1/integrations/n8n/supplier-quotes",
        content=body,
        headers=stale,
    )
    assert expired.status_code == 401
    assert "replay window" in expired.text


def test_supplier_quote_callback_ingests_and_replays(api: Any) -> None:
    body = json.dumps({"event_id": "evt-1", "quotes": [make_quote().model_dump(mode="json")]}).encode()
    headers = _signed_callback(body)

    first = api.client.post(
        "/api/v1/integrations/n8n/supplier-quotes",
        content=body,
        headers=headers,
    )
    second = api.client.post(
        "/api/v1/integrations/n8n/supplier-quotes",
        content=body,
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "received"
    assert first.json()["created"] == [True]
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert len(api.quotes.saved) == 1
    assert api.integrations.records["quote-key-0001"].status == "succeeded"
    assert api.audit.entries[-1]["event_type"].value == "SUPPLIER_QUOTE_INGESTED"


def test_approval_creates_erpnext_quotation(api: Any) -> None:
    signed = sign_offer(make_offer(), PRIVATE_KEY, approver="C. Mueller", approved_at=NOW)

    response = api.client.post(
        "/api/v1/approvals/approve",
        json={
            "offer": signed.model_dump(mode="json"),
            "signature": signed.ed25519_approval_signature,
            "approver": "C. Mueller",
            "approved_at": "2026-01-15T09:00:00Z",
        },
        headers=auth(APPROVER_TOKEN),
    )

    assert response.status_code == 200, response.text
    assert response.json()["erpnext"]["status"] == "created"
    assert response.json()["offer"]["erpnext_quotation_name"] == "QTN-2026-0001"
    assert len(api.erpnext.offers) == 1
    assert api.audit.entries[-1]["event_type"].value == "ERP_QUOTATION_CREATED"
