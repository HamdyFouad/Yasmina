"""Offer persistence, idempotent approval and ownership API tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_service,
    get_erpnext_client,
    get_invoice_repository,
    get_object_storage,
    get_offer_repository,
    get_quotation_sync_repository,
    get_session,
    get_workflow_checkpointer,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.integrations.erpnext_client import ERPQuotation
from src.main import create_app
from src.models.enums import CustomerOfferStatus, UnitOfMeasure
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.repositories.offer_repository import StoredOffer
from src.services.approval_service import generate_keypair, sign_offer

PRIVATE_KEY, PUBLIC_KEY = generate_keypair()
JWT_SECRET = "test-offer-jwt-secret-with-at-least-32-bytes"
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        jwt_secret_key=JWT_SECRET,
        api_admin_password="test-admin-password",
        ed25519_approval_public_key=PUBLIC_KEY,
        company_name="Yasmina GmbH",
        company_street="Hauptstr. 1",
        company_city="Berlin",
        company_postal_code="10115",
        company_vat_id="DE123456789",
        company_iban="DE89370400440532013000",
        company_email="rechnung@bayerclean.de",
        company_phone="+49 941 123456",
    )


SETTINGS = make_settings()


def token(subject: str, role: Role) -> str:
    return create_access_token(SETTINGS, subject, [role])


SALES_A = token("sales-a", Role.SALES)
SALES_B = token("sales-b", Role.SALES)
VIEWER = token("viewer-user", Role.VIEWER)
ADMIN = token("admin-user", Role.ADMIN)
APPROVER = token("approver-user", Role.APPROVER)


def auth(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def make_offer(
    *,
    offer_number: str = "AN-2026-0100",
    customer: str = "Stadt Muster",
    tender_id: UUID | None = None,
) -> CustomerOffer:
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
        customer_name=customer,
        customer_contact_email="einkauf@example.com",
        tender_id=tender_id,
        status=CustomerOfferStatus.DRAFT,
        lines=[line],
        margin_rate=margin,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
    )


def signed_offer(*, offer_number: str = "AN-2026-0100", customer: str = "Stadt Muster") -> CustomerOffer:
    return sign_offer(
        make_offer(offer_number=offer_number, customer=customer),
        PRIVATE_KEY,
        approver="C. Mueller",
        approved_at=NOW,
    )


class FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeInvoiceRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, Any] = {}

    async def get_by_offer(self, offer_id: UUID) -> Any | None:
        return self.records.get(offer_id)

    async def create(self, invoice: Any, *, offer_id: UUID, actor: str | None = None) -> Any:
        numbered = invoice.model_copy(update={"invoice_number": "INV-2026-0001"})
        record = SimpleNamespace(id=uuid4(), invoice=numbered)
        self.records[offer_id] = record
        return record

    async def attach_artifacts(self, invoice_id: UUID, artifacts: dict[str, Any]) -> Any:
        return None


class FakeOfferRepository:
    def __init__(self) -> None:
        self.offers: dict[str, CustomerOffer] = {}

    async def get_by_number(self, offer_number: str) -> StoredOffer | None:
        offer = self.offers.get(offer_number)
        return StoredOffer(offer=offer, created=False) if offer is not None else None

    async def get(self, offer_id: UUID) -> StoredOffer | None:
        for offer in self.offers.values():
            if offer.id == offer_id:
                return StoredOffer(offer=offer, created=False)
        return None

    async def save(self, offer: CustomerOffer, *, commit: bool = True) -> StoredOffer:
        created = offer.offer_number not in self.offers
        self.offers[offer.offer_number] = offer
        return StoredOffer(offer=offer, created=created)

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        owner: str | None = None,
        status: CustomerOfferStatus | None = None,
        tender_id: UUID | None = None,
    ) -> list[CustomerOffer]:
        values = list(self.offers.values())
        if owner is not None:
            values = [offer for offer in values if offer.owner == owner]
        if status is not None:
            values = [offer for offer in values if offer.status == status]
        if tender_id is not None:
            values = [offer for offer in values if offer.tender_id == tender_id]
        return values[offset : offset + limit]

    async def count(
        self,
        *,
        owner: str | None = None,
        status: CustomerOfferStatus | None = None,
        tender_id: UUID | None = None,
    ) -> int:
        values = list(self.offers.values())
        if owner is not None:
            values = [offer for offer in values if offer.owner == owner]
        if status is not None:
            values = [offer for offer in values if offer.status == status]
        if tender_id is not None:
            values = [offer for offer in values if offer.tender_id == tender_id]
        return len(values)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = data
        return object_name

    async def exists(self, object_name: str) -> bool:
        return object_name in self.objects

    async def get_bytes(self, object_name: str) -> bytes:
        return self.objects[object_name]


class FakeERPNext:
    configured = True

    def __init__(self) -> None:
        self.calls: list[CustomerOffer] = []

    async def create_quotation(self, offer: CustomerOffer) -> ERPQuotation:
        self.calls.append(offer)
        return ERPQuotation(
            name="QTN-2026-0001",
            title=f"Yasmina {offer.offer_number}",
            created=True,
            payload={},
        )


class FakeAudit:
    async def append(self, **kwargs: Any) -> None:
        return None


class FakeQuotationSyncRepository:
    def __init__(self) -> None:
        self.jobs: dict[UUID, Any] = {}

    async def enqueue(
        self,
        offer_id: UUID,
        *,
        offer_number: str,
        max_attempts: int = 6,
        commit: bool = True,
    ) -> tuple[Any, bool]:
        existing = self.jobs.get(offer_id)
        if existing is not None:
            return existing, False
        job = SimpleNamespace(
            id=uuid4(),
            offer_id=offer_id,
            offer_number=offer_number,
            status="pending",
            attempts=0,
            max_attempts=max_attempts,
        )
        self.jobs[offer_id] = job
        return job, True


@pytest.fixture()
def api() -> Any:
    app = create_app(SETTINGS, run_startup_checks=False)
    repository = FakeOfferRepository()
    storage = FakeStorage()
    erpnext = FakeERPNext()
    session = FakeSession()
    sync_jobs = FakeQuotationSyncRepository()

    async def fake_session() -> Any:
        yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_offer_repository] = lambda: repository
    app.dependency_overrides[get_invoice_repository] = lambda: FakeInvoiceRepository()
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_erpnext_client] = lambda: erpnext
    app.dependency_overrides[get_audit_service] = lambda: FakeAudit()
    app.dependency_overrides[get_quotation_sync_repository] = lambda: sync_jobs
    app.dependency_overrides[get_workflow_checkpointer] = lambda: None

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            repository=repository,
            storage=storage,
            erpnext=erpnext,
            sync_jobs=sync_jobs,
        )


def approval_body(offer: CustomerOffer, *, owner: str = "sales-a") -> dict[str, Any]:
    return {
        "offer": offer.model_dump(mode="json"),
        "signature": offer.ed25519_approval_signature,
        "approver": "C. Mueller",
        "approved_at": "2026-01-15T09:00:00Z",
        "owner": owner,
        "invoice": {
            "buyer": {
                "street": "Rathausstr. 2",
                "city": "Musterstadt",
                "postal_code": "12345",
            },
            "buyer_reference": "991-03730-19",
            "due_date": "2026-02-14",
        },
    }


def test_four_eyes_rejects_sales_owner_as_approver(api: Any) -> None:
    offer = signed_offer()
    owner_approver = create_access_token(
        SETTINGS,
        "sales-a",
        [Role.SALES, Role.APPROVER],
    )

    response = api.client.post(
        "/api/v1/approvals/approve",
        json=approval_body(offer, owner="sales-a"),
        headers=auth(owner_approver),
    )

    assert response.status_code == 403
    assert "four-eyes" in response.text
    assert offer.offer_number not in api.repository.offers


def test_approval_persists_and_replays_idempotently(api: Any) -> None:
    offer = signed_offer()
    first = api.client.post(
        "/api/v1/approvals/approve",
        json=approval_body(offer),
        headers=auth(APPROVER),
    )
    second = api.client.post(
        "/api/v1/approvals/approve",
        json=approval_body(offer),
        headers=auth(APPROVER),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["offer"]["owner"] == "sales-a"
    assert first.json()["erpnext"]["status"] == "queued"
    assert first.json()["erpnext"]["created"] is True
    assert second.json()["erpnext"]["status"] == "queued"
    assert second.json()["erpnext"]["created"] is False
    assert first.json()["erpnext"]["job_id"] == second.json()["erpnext"]["job_id"]
    assert len(api.erpnext.calls) == 0
    assert len(api.sync_jobs.jobs) == 1
    assert len(api.storage.objects) == 2  # XRechnung XML + ZUGFeRD PDF

    persisted = api.repository.offers[offer.offer_number]
    assert persisted.status is CustomerOfferStatus.APPROVED
    assert persisted.owner == "sales-a"
    assert persisted.erpnext_quotation_name is None
    assert set(persisted.invoice_artifacts) == {"xrechnung", "zugferd"}
    assert api.storage.objects[persisted.invoice_artifacts["xrechnung"]["object_name"]]
    assert api.storage.objects[persisted.invoice_artifacts["zugferd"]["object_name"]].startswith(b"%PDF-")


def test_same_offer_number_with_different_signature_conflicts(api: Any) -> None:
    first = signed_offer()
    second = signed_offer(customer="Evil GmbH")
    assert first.offer_number == second.offer_number

    accepted = api.client.post("/api/v1/approvals/approve", json=approval_body(first), headers=auth(APPROVER))
    rejected = api.client.post(
        "/api/v1/approvals/approve", json=approval_body(second), headers=auth(APPROVER)
    )

    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 409
    assert "different" in rejected.text


def test_offer_read_routes_enforce_sales_ownership(api: Any) -> None:
    offer = signed_offer(offer_number="AN-2026-0200")
    approved = api.client.post(
        "/api/v1/approvals/approve",
        json=approval_body(offer, owner="sales-a"),
        headers=auth(APPROVER),
    )
    assert approved.status_code == 200, approved.text
    offer_id = approved.json()["offer"]["id"]

    own = api.client.get(f"/api/v1/offers/{offer_id}", headers=auth(SALES_A))
    other = api.client.get(f"/api/v1/offers/{offer_id}", headers=auth(SALES_B))
    viewer = api.client.get(f"/api/v1/offers/{offer_id}", headers=auth(VIEWER))
    admin = api.client.get(f"/api/v1/offers/{offer_id}", headers=auth(ADMIN))

    assert own.status_code == 200
    assert other.status_code == 403
    assert viewer.status_code == 200
    assert admin.status_code == 200
    assert own.json()["offer"]["offer_number"] == "AN-2026-0200"

    own_list = api.client.get("/api/v1/offers", headers=auth(SALES_A))
    other_list = api.client.get("/api/v1/offers", headers=auth(SALES_B))
    viewer_list = api.client.get("/api/v1/offers", headers=auth(VIEWER))
    assert own_list.json()["count"] == 1
    assert other_list.json()["count"] == 0
    assert viewer_list.json()["count"] == 1


def test_missing_offer_is_404(api: Any) -> None:
    response = api.client.get(f"/api/v1/offers/{uuid4()}", headers=auth(VIEWER))
    assert response.status_code == 404


def test_offers_can_be_filtered_by_tender(api: Any) -> None:
    tender_id = uuid4()
    matching = make_offer(offer_number="AN-2026-0300", tender_id=tender_id).model_copy(
        update={"owner": "sales-a"}
    )
    unrelated = make_offer(offer_number="AN-2026-0301", tender_id=uuid4()).model_copy(
        update={"owner": "sales-a"}
    )
    api.repository.offers[matching.offer_number] = matching
    api.repository.offers[unrelated.offer_number] = unrelated

    response = api.client.get(
        "/api/v1/offers",
        params={"tender_id": str(tender_id)},
        headers=auth(SALES_A),
    )

    assert response.status_code == 200, response.text
    assert [offer["offer_number"] for offer in response.json()["offers"]] == ["AN-2026-0300"]
    assert response.json()["count"] == 1
