"""Live-mocked FastAPI/API integration tests.

The app runs with startup preflight disabled and an injected fake DB session for
the pgvector matching route. GAEB parsing, the LangGraph workflow and Ed25519
approval use their real implementations.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_service,
    get_clamav_scanner,
    get_object_storage,
    get_offer_repository,
    get_session,
    get_tender_repository,
    get_workflow_checkpointer,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.integrations.clamav_client import ClamAVResult
from src.main import create_app
from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.models.schemas import CustomerOffer, SupplierQuote, SupplierQuoteLine
from src.repositories.offer_repository import StoredOffer
from src.repositories.tender_repository import StoredTender
from src.services.approval_service import generate_keypair, sign_offer

pygaeb = pytest.importorskip("pygaeb")

from pygaeb import ExchangePhase, GAEBWriter  # noqa: E402
from pygaeb.builder import BoQBuilder  # noqa: E402

PRIVATE_KEY_HEX, PUBLIC_KEY_HEX = generate_keypair()
TEST_JWT_SECRET = "test-jwt-secret-key-with-at-least-32-bytes"
SALES_TOKEN = create_access_token(
    Settings(_env_file=None, database_url=None, jwt_secret_key=TEST_JWT_SECRET),
    "sales-user",
    [Role.SALES],
)
APPROVER_TOKEN = create_access_token(
    Settings(_env_file=None, database_url=None, jwt_secret_key=TEST_JWT_SECRET),
    "approver-user",
    [Role.APPROVER],
)


def build_x83(path: Path) -> Path:
    builder = BoQBuilder(phase="X83")
    builder.project(project_name="Rahmenvertrag", client="Stadt Muster", award_no="T-1", currency="EUR")
    lot = builder.add_lot("1", "Los 1")
    category = lot.add_category("1", "Reinigung")
    category.add_item(
        oz="1.1",
        short_text="Nitrilhandschuhe blau Gr. M",
        qty=Decimal("10"),
        unit="Stk",
    )
    document = builder.build()
    GAEBWriter.write(document, path, phase=ExchangePhase.X83)
    return path


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        jwt_secret_key=TEST_JWT_SECRET,
        api_admin_password="test-admin-password",
        ed25519_approval_public_key=PUBLIC_KEY_HEX,
        company_name="Yasmina GmbH",
        company_street="Hauptstr. 1",
        company_city="Berlin",
        company_postal_code="10115",
        company_vat_id="DE123456789",
        company_iban="DE89370400440532013000",
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def quote_payload(item_id: str) -> dict[str, Any]:
    quote = SupplierQuote(
        supplier_name="Hygiene Gross GmbH",
        quote_reference="ANG-77",
        lines=[
            SupplierQuoteLine(
                line_number=1,
                description="Nitrilhandschuhe blau Gr. M",
                tender_item_id=UUID(item_id),
                quantity=Decimal("10.0000"),
                unit=UnitOfMeasure.STUECK,
                unit_price_net=Decimal("10.0000"),
            )
        ],
    )
    return quote.model_dump(mode="json")


def offer_request_payload() -> dict[str, Any]:
    return {
        "customer_name": "Stadt Muster",
        "offer_number": "AN-2026-0001",
        "valid_until": "2026-12-31",
        "margin_rate": "0.15",
        "vat_rate": "0.19",
    }


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statement: Any = None

    async def execute(self, statement: Any) -> FakeResult:
        self.statement = statement
        return FakeResult(self.rows)


def make_candidate_rows() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id="article-1",
            article_number="ART-1",
            description="Nitril Einweghandschuhe blau Größe M",
            material=MaterialType.NITRILE,
            color=StandardizedColor.BLUE,
            glove_size=GloveSize.M,
            unit=UnitOfMeasure.STUECK,
            pack_size=1000,
            unit_price_net=Decimal("10.00"),
            embedding=[0.0] * 1024,
        )
    ]


class FakeTenderRepository:
    def __init__(self) -> None:
        self.items: dict[Any, StoredTender] = {}

    async def save(self, tender, *, source_filename=None, source_path=None, source_object=None, document=None):  # type: ignore[no-untyped-def]
        stored = StoredTender(
            tender=tender,
            document=document,
            source_filename=source_filename,
            source_path=source_path,
            source_object=source_object,
        )
        self.items[tender.id] = stored
        return stored

    async def get(self, tender_id):  # type: ignore[no-untyped-def]
        return self.items.get(tender_id)


class CleanScanner:
    async def scan_bytes(self, data: bytes) -> ClamAVResult:
        return ClamAVResult(clean=True, signature=None, raw="stream: OK")


class InfectedScanner:
    async def scan_bytes(self, data: bytes) -> ClamAVResult:
        return ClamAVResult(clean=False, signature="Eicar-Test-Signature", raw="stream: Eicar FOUND")


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = data
        return object_name

    async def get_bytes(self, object_name: str) -> bytes:
        return self.objects[object_name]

    async def delete(self, object_name: str) -> None:
        self.objects.pop(object_name, None)

    async def exists(self, object_name: str) -> bool:
        return object_name in self.objects


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def record_clamav(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


class FakeOfferRepository:
    def __init__(self) -> None:
        self.offers: dict[str, CustomerOffer] = {}

    async def get_by_number(self, offer_number: str) -> StoredOffer | None:
        offer = self.offers.get(offer_number)
        return StoredOffer(offer=offer, created=False) if offer is not None else None

    async def get(self, offer_id):  # type: ignore[no-untyped-def]
        for offer in self.offers.values():
            if offer.id == offer_id:
                return StoredOffer(offer=offer, created=False)
        return None

    async def save(self, offer: CustomerOffer, *, commit: bool = True) -> StoredOffer:
        created = offer.offer_number not in self.offers
        self.offers[offer.offer_number] = offer
        return StoredOffer(offer=offer, created=created)

    async def list(self, **kwargs):  # type: ignore[no-untyped-def]
        return list(self.offers.values())

    async def count(self, **kwargs) -> int:  # type: ignore[no-untyped-def]
        return len(self.offers)


@pytest.fixture()
def api(workspace_tmp):  # type: ignore[no-untyped-def]
    app = create_app(make_settings(), run_startup_checks=False)
    fake_session = FakeSession(make_candidate_rows())
    repository = FakeTenderRepository()
    storage = FakeStorage()
    audit = FakeAudit()
    offers = FakeOfferRepository()

    async def fake_get_session():
        yield fake_session

    app.dependency_overrides[get_session] = fake_get_session
    app.dependency_overrides[get_tender_repository] = lambda: repository
    app.dependency_overrides[get_clamav_scanner] = lambda: CleanScanner()
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_audit_service] = lambda: audit
    app.dependency_overrides[get_offer_repository] = lambda: offers
    app.dependency_overrides[get_workflow_checkpointer] = lambda: None
    app.state.test_audit = audit
    app.state.test_offers = offers
    with TestClient(app) as client:
        yield client, app, fake_session


def upload_gaeb(client: TestClient, workspace_tmp: Path) -> dict[str, Any]:
    path = build_x83(workspace_tmp / "tender.X83")
    response = client.post(
        "/api/v1/tenders/upload",
        files={"file": ("tender.X83", path.read_bytes(), "application/xml")},
        headers=auth(SALES_TOKEN),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_auth_token_issue(api) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = api
    response = client.post(
        "/api/v1/auth/token",
        json={"username": "admin", "password": "test-admin-password"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]


def test_end_to_end_langgraph_transition(api, workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    client, app, _ = api
    uploaded = upload_gaeb(client, workspace_tmp)
    tender_id = uploaded["tender_id"]
    item_id = uploaded["tender"]["items"][0]["id"]
    assert app.state.test_audit.entries[0]["clean"] is True

    body = {
        "supplier_quotes": [quote_payload(item_id)],
        "offer_request": offer_request_payload(),
    }
    pending = client.post(f"/api/v1/tenders/{tender_id}/workflow", json=body, headers=auth(SALES_TOKEN))
    assert pending.status_code == 200, pending.text
    pending_payload = pending.json()
    assert pending_payload["status"] == "pending_approval"
    assert pending_payload["offer"]["status"] == "DRAFT"

    offer = CustomerOffer.model_validate_json(json.dumps(pending_payload["offer"]))
    signed = sign_offer(
        offer,
        PRIVATE_KEY_HEX,
        approver="C. Mueller",
        approved_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
    )

    approved_payload = {
        **body,
        "offer": signed.model_dump(mode="json"),
    }
    approved = client.post(
        f"/api/v1/tenders/{tender_id}/workflow",
        json=approved_payload,
        headers=auth(SALES_TOKEN),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["offer"]["status"] == "APPROVED"


def test_match_uses_pgvector_route(api, workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    client, _, fake_session = api
    uploaded = upload_gaeb(client, workspace_tmp)
    tender_id = uploaded["tender_id"]
    query = [0.0] * 1024

    response = client.post(
        f"/api/v1/tenders/{tender_id}/match",
        json={"query_embedding": query, "limit": 3},
        headers=auth(SALES_TOKEN),
    )

    assert response.status_code == 200, response.text
    assert response.json()["matches"]
    assert fake_session.statement is not None
    assert "<=>" in str(fake_session.statement)


def test_pricing_route(api, workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = api
    uploaded = upload_gaeb(client, workspace_tmp)
    item_id = uploaded["tender"]["items"][0]["id"]

    response = client.post(
        f"/api/v1/tenders/{uploaded['tender_id']}/price",
        json={"supplier_quotes": [quote_payload(item_id)], "offer_request": offer_request_payload()},
        headers=auth(SALES_TOKEN),
    )

    assert response.status_code == 200, response.text
    assert response.json()["offer"]["total_net"] == "115.00"


def test_approval_rbac_and_verification(api, workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = api
    uploaded = upload_gaeb(client, workspace_tmp)
    item_id = uploaded["tender"]["items"][0]["id"]
    body = {
        "supplier_quotes": [quote_payload(item_id)],
        "offer_request": offer_request_payload(),
    }
    pending = client.post(
        f"/api/v1/tenders/{uploaded['tender_id']}/workflow",
        json=body,
        headers=auth(SALES_TOKEN),
    ).json()
    offer = CustomerOffer.model_validate_json(json.dumps(pending["offer"]))
    signed = sign_offer(
        offer,
        PRIVATE_KEY_HEX,
        approver="C. Mueller",
        approved_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
    )

    # Sales role cannot approve.
    forbidden = client.post(
        "/api/v1/approvals/approve",
        json={
            "offer": signed.model_dump(mode="json"),
            "signature": signed.ed25519_approval_signature,
            "approver": "C. Mueller",
            "approved_at": "2026-01-15T09:00:00Z",
        },
        headers=auth(SALES_TOKEN),
    )
    assert forbidden.status_code == 403

    approved = client.post(
        "/api/v1/approvals/approve",
        json={
            "offer": signed.model_dump(mode="json"),
            "signature": signed.ed25519_approval_signature,
            "approver": "C. Mueller",
            "approved_at": "2026-01-15T09:00:00Z",
            "invoice": {
                "buyer": {
                    "street": "Rathausstr. 2",
                    "city": "Musterstadt",
                    "postal_code": "12345",
                },
                "buyer_reference": "991-03730-19",
                "due_date": "2026-02-14",
                "iban": "DE89370400440532013000",
            },
        },
        headers=auth(APPROVER_TOKEN),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["artifacts"]["xrechnung"]["object_name"].endswith("invoice.xml")

    verify = client.post(
        "/api/v1/approvals/verify",
        json={"offer": approved.json()["offer"]},
        headers=auth(APPROVER_TOKEN),
    )
    assert verify.status_code == 200
    assert verify.json()["valid"] is True

    xml = client.get(
        f"/api/v1/approvals/{offer.offer_number}/documents/xrechnung",
        headers=auth(APPROVER_TOKEN),
    )
    assert xml.status_code == 200
    assert b"CrossIndustryInvoice" not in xml.content  # XRechnung is UBL
    assert b"<Invoice" in xml.content

    pdf = client.get(
        f"/api/v1/approvals/{offer.offer_number}/documents/zugferd",
        headers=auth(APPROVER_TOKEN),
    )
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")


def test_infected_upload_is_rejected(api, workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    client, app, _ = api
    app.dependency_overrides[get_clamav_scanner] = lambda: InfectedScanner()
    path = build_x83(workspace_tmp / "infected.X83")

    response = client.post(
        "/api/v1/tenders/upload",
        files={"file": ("infected.X83", path.read_bytes(), "application/xml")},
        headers=auth(SALES_TOKEN),
    )

    assert response.status_code == 422
    assert "malware detected" in response.text
    assert app.state.test_audit.entries[-1]["clean"] is False
    assert app.state.test_audit.entries[-1]["signature"] == "Eicar-Test-Signature"


def test_missing_bearer_is_401(api) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = api
    response = client.post(
        "/api/v1/tenders/upload",
        files={"file": ("x.x83", b"not xml", "application/xml")},
    )
    assert response.status_code == 401
