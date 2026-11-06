"""Billing API route tests with injected repositories (no live database)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from src.api.deps import (
    get_credit_note_repository,
    get_invoice_repository,
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
        self.payments: list[tuple[UUID, Any, str | None]] = []
        self.record = SimpleNamespace(
            as_dict=lambda: {
                "id": str(self.invoice_id),
                "invoice_number": "INV-2026-0001",
                "status": "ISSUED",
                "total_gross": "119.00",
            }
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
    async def get(self, offer_id: UUID) -> Any:
        return SimpleNamespace(offer=SimpleNamespace(owner="sales-a"))


def make_client() -> tuple[TestClient, FakeInvoiceRepository, FakeCreditNoteRepository, str]:
    settings = make_settings()
    invoices = FakeInvoiceRepository()
    credits = FakeCreditNoteRepository()
    app = create_app(settings, run_startup_checks=False)
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_invoice_repository] = lambda: invoices
    app.dependency_overrides[get_credit_note_repository] = lambda: credits
    app.dependency_overrides[get_offer_repository] = lambda: FakeOfferRepository()
    token = create_access_token(settings, "admin-user", [Role.ADMIN])
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
