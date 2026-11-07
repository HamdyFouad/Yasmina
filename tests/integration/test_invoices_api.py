"""Billing API route tests with injected repositories (no live database)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from src.api.deps import (
    get_credit_note_repository,
    get_invoice_repository,
    get_object_storage,
    get_offer_repository,
    get_session,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        app_env="test",
        jwt_secret_key="billing-api-jwt-secret-with-32-bytes",
        api_admin_password="test-admin-password",
    )


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeInvoiceRepository:
    def __init__(self) -> None:
        self.invoice_id = uuid4()
        self.offer_id = uuid4()
        self.payments: list[tuple[UUID, Any, str | None]] = []
        self.record = SimpleNamespace(
            offer_id=self.offer_id,
            invoice=SimpleNamespace(invoice_number="INV-2026-0001"),
            as_dict=lambda: {
                "id": str(self.invoice_id),
                "invoice_number": "INV-2026-0001",
                "status": "ISSUED",
                "total_gross": "119.00",
            },
        )

    async def list(self, **kwargs: Any) -> list[Any]:
        return [self.record]

    async def count(self, **kwargs: Any) -> int:
        return 1

    async def get_by_id(self, invoice_id: UUID) -> Any:
        return self.record if invoice_id == self.invoice_id else None

    async def events(self, invoice_id: UUID) -> list[dict[str, Any]]:
        return [{"event_type": "ISSUED", "sequence": 1}]

    async def verify_event_chain(self, invoice_id: UUID) -> bool:
        return True

    async def record_payment(
        self,
        invoice_id: UUID,
        amount: Any,
        *,
        actor: str | None = None,
        reference: str | None = None,
    ) -> Any:
        self.payments.append((invoice_id, amount, reference))
        self.record.as_dict = lambda: {
            "id": str(invoice_id),
            "invoice_number": "INV-2026-0001",
            "status": "PARTIALLY_PAID",
            "total_gross": "119.00",
        }
        return self.record


class FakeCreditNoteRepository:
    async def issue(self, invoice_id: UUID, **kwargs: Any) -> Any:
        return SimpleNamespace(
            as_dict=lambda: {
                "credit_note_number": "GS-2026-0001",
                "invoice_id": str(invoice_id),
                "total_gross": "26.00",
            }
        )


class FakeOfferRepository:
    def __init__(self, *, owner: str = "sales-a") -> None:
        self.owner = owner

    async def get(self, offer_id: UUID) -> Any:
        return SimpleNamespace(offer=SimpleNamespace(owner=self.owner, offer_number="ANG-2026-0001"))


class FakeStorage:
    def __init__(self, *, present: bool = True) -> None:
        self.present = present
        self.requested: list[str] = []

    async def exists(self, object_name: str) -> bool:
        return self.present

    async def get_bytes(self, object_name: str) -> bytes:
        self.requested.append(object_name)
        return b"%PDF-1.7 invoice"


def make_client(
    *,
    offer_owner: str = "sales-a",
    storage_present: bool = True,
    subject: str = "admin-user",
    roles: list[Role] | None = None,
) -> tuple[TestClient, FakeInvoiceRepository, FakeCreditNoteRepository, str]:
    settings = make_settings()
    invoices = FakeInvoiceRepository()
    credits = FakeCreditNoteRepository()
    app = create_app(settings, run_startup_checks=False)
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_invoice_repository] = lambda: invoices
    app.dependency_overrides[get_credit_note_repository] = lambda: credits
    app.dependency_overrides[get_offer_repository] = lambda: FakeOfferRepository(owner=offer_owner)
    app.dependency_overrides[get_object_storage] = lambda: FakeStorage(present=storage_present)
    token = create_access_token(settings, subject, roles or [Role.ADMIN])
    return TestClient(app), invoices, credits, token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_invoice_list_and_payment_endpoints() -> None:
    client, invoices, _, token = make_client()
    with client:
        listed = client.get("/api/v1/invoices", headers=auth(token))
        assert listed.status_code == 200, listed.text
        assert listed.json()["count"] == 1

        paid = client.post(
            f"/api/v1/invoices/{invoices.invoice_id}/payments",
            json={"amount": "50.00", "reference": "BANK-1"},
            headers=auth(token),
        )
        assert paid.status_code == 200, paid.text
        assert paid.json()["invoice"]["status"] == "PARTIALLY_PAID"
        assert invoices.payments[0][1] == 50

        credit = client.post(
            f"/api/v1/invoices/{invoices.invoice_id}/credits",
            json={"amount": "26.00", "reason": "Retoure"},
            headers=auth(token),
        )
        assert credit.status_code == 200, credit.text
        assert credit.json()["credit_note"]["credit_note_number"] == "GS-2026-0001"


def test_invoice_detail_and_events() -> None:
    client, invoices, _, token = make_client()
    with client:
        response = client.get(f"/api/v1/invoices/{invoices.invoice_id}", headers=auth(token))
        assert response.status_code == 200, response.text
        assert response.json()["event_chain_valid"] is True
        assert response.json()["events"][0]["event_type"] == "ISSUED"


def test_invoice_document_downloads_resolve_the_offer_number() -> None:
    client, invoices, _, token = make_client()
    with client:
        pdf = client.get(
            f"/api/v1/invoices/{invoices.invoice_id}/documents/zugferd",
            headers=auth(token),
        )
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"] == "application/pdf"
        assert 'filename="INV-2026-0001.pdf"' in pdf.headers["content-disposition"]
        assert pdf.content.startswith(b"%PDF")

        xml = client.get(
            f"/api/v1/invoices/{invoices.invoice_id}/documents/xrechnung",
            headers=auth(token),
        )
        assert xml.status_code == 200, xml.text
        assert xml.headers["content-type"] == "application/xml"
        assert 'filename="INV-2026-0001.xml"' in xml.headers["content-disposition"]

        unknown_format = client.get(
            f"/api/v1/invoices/{invoices.invoice_id}/documents/paper",
            headers=auth(token),
        )
        assert unknown_format.status_code == 404
        assert "zugferd" in unknown_format.json()["detail"]

        unknown_invoice = client.get(
            f"/api/v1/invoices/{uuid4()}/documents/zugferd",
            headers=auth(token),
        )
        assert unknown_invoice.status_code == 404


def test_invoice_documents_report_ungenerated_files() -> None:
    client, invoices, _, token = make_client(storage_present=False)
    with client:
        response = client.get(
            f"/api/v1/invoices/{invoices.invoice_id}/documents/zugferd",
            headers=auth(token),
        )
        assert response.status_code == 404
        assert "generated" in response.json()["detail"]


def test_sales_users_only_download_their_own_invoice_documents() -> None:
    client, invoices, _, token = make_client(
        offer_owner="sales-a",
        subject="sales-a",
        roles=[Role.SALES],
    )
    with client:
        allowed = client.get(
            f"/api/v1/invoices/{invoices.invoice_id}/documents/xrechnung",
            headers=auth(token),
        )
        assert allowed.status_code == 200, allowed.text

    other, other_invoices, _, other_token = make_client(
        offer_owner="sales-b",
        subject="sales-a",
        roles=[Role.SALES],
    )
    with other:
        forbidden = other.get(
            f"/api/v1/invoices/{other_invoices.invoice_id}/documents/xrechnung",
            headers=auth(other_token),
        )
        assert forbidden.status_code == 403
