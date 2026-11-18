"""Supplier/customer master-data API tests with mocked repositories."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_service,
    get_customer_repository,
    get_session,
    get_supplier_repository,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.repositories.master_data_repository import (
    CustomerInput,
    CustomerView,
    SupplierContactView,
    SupplierInput,
    SupplierView,
)

SETTINGS = Settings(
    _env_file=None,
    database_url=None,
    app_env="test",
    jwt_secret_key="master-data-jwt-secret-with-32-bytes",
    api_admin_password="test-admin-password",
)
ADMIN = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'admin', [Role.ADMIN])}"}
SALES = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'sales', [Role.SALES])}"}
NOW = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def make_supplier(name: str = "Hygiene Gross") -> SupplierView:
    return SupplierView(
        id=uuid4(),
        name=name,
        legal_name=f"{name} GmbH",
        erpnext_supplier_id=None,
        email="rfq@hygiene-gross.de",
        phone="+49 941 123456",
        street="Gewerbepark 7",
        postal_code="93055",
        city="Regensburg",
        country_code="DE",
        payment_terms="Payable within 30 days",
        delivery_days=5,
        notes=None,
        is_active=True,
        contacts=(
            SupplierContactView(
                id=uuid4(),
                name="Erika Muster",
                role="Key Account",
                email="rfq@hygiene-gross.de",
                phone="+49 941 123456",
                is_primary=True,
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def make_customer(name: str = "Stadt Muster") -> CustomerView:
    return CustomerView(
        id=uuid4(),
        name=name,
        legal_name=f"{name} Stadt",
        street="Rathausplatz 1",
        postal_code="93047",
        city="Regensburg",
        country_code="DE",
        vat_id="DE123456789",
        leitweg_id="991-03730-19",
        payment_terms="Payable within 14 days",
        contact_name="Einkauf",
        contact_email="einkauf@musterstadt.de",
        phone=None,
        notes=None,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeSuppliers:
    def __init__(self) -> None:
        self.suppliers = [make_supplier()]
        self.created: SupplierInput | None = None
        self.updated: SupplierInput | None = None

    async def list_suppliers(self, **kwargs: Any) -> list[SupplierView]:
        return self.suppliers

    async def count_suppliers(self, **kwargs: Any) -> int:
        return len(self.suppliers)

    async def get_supplier(self, supplier_id: UUID) -> SupplierView | None:
        return next((item for item in self.suppliers if item.id == supplier_id), None)

    async def create_supplier(self, payload: SupplierInput) -> SupplierView:
        self.created = payload
        supplier = make_supplier(payload.name)
        self.suppliers.append(supplier)
        return supplier

    async def update_supplier(self, supplier_id: UUID, payload: SupplierInput) -> SupplierView:
        self.updated = payload
        return make_supplier(payload.name)

    async def deactivate_supplier(self, supplier_id: UUID) -> SupplierView:
        return replace(make_supplier(), is_active=False)


class FakeCustomers:
    def __init__(self) -> None:
        self.customers = [make_customer()]
        self.created: CustomerInput | None = None

    async def list_customers(self, **kwargs: Any) -> list[CustomerView]:
        return self.customers

    async def count_customers(self, **kwargs: Any) -> int:
        return len(self.customers)

    async def get_customer(self, customer_id: UUID) -> CustomerView | None:
        return next((item for item in self.customers if item.id == customer_id), None)

    async def create_customer(self, payload: CustomerInput) -> CustomerView:
        self.created = payload
        customer = make_customer(payload.name)
        self.customers.append(customer)
        return customer

    async def update_customer(self, customer_id: UUID, payload: CustomerInput) -> CustomerView:
        return make_customer(payload.name)

    async def deactivate_customer(self, customer_id: UUID) -> CustomerView:
        return replace(make_customer(), is_active=False)


def make_client() -> tuple[TestClient, FakeSuppliers, FakeCustomers, FakeAudit]:
    app = create_app(SETTINGS, run_startup_checks=False)
    suppliers = FakeSuppliers()
    customers = FakeCustomers()
    audit = FakeAudit()
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_supplier_repository] = lambda: suppliers
    app.dependency_overrides[get_customer_repository] = lambda: customers
    app.dependency_overrides[get_audit_service] = lambda: audit
    return TestClient(app), suppliers, customers, audit


def supplier_payload() -> dict[str, Any]:
    return {
        "name": "Hygiene Gross",
        "legal_name": "Hygiene Gross GmbH",
        "email": "rfq@hygiene-gross.de",
        "country_code": "de",
        "payment_terms": "Payable within 30 days",
        "delivery_days": 5,
        "is_active": True,
        "contacts": [
            {
                "name": "Erika Muster",
                "role": "Key Account",
                "email": "rfq@hygiene-gross.de",
                "is_primary": True,
            }
        ],
    }


def test_master_data_reads_are_authenticated_and_writes_are_admin_only() -> None:
    client, _, _, _ = make_client()
    with client:
        suppliers = client.get("/api/v1/suppliers", headers=SALES)
        customers = client.get("/api/v1/customers", headers=SALES)
        forbidden = client.post("/api/v1/suppliers", json=supplier_payload(), headers=SALES)

    assert suppliers.status_code == 200, suppliers.text
    assert suppliers.json()["count"] == 1
    assert customers.status_code == 200, customers.text
    assert customers.json()["count"] == 1
    assert forbidden.status_code == 403


def test_admin_can_create_update_and_deactivate_suppliers() -> None:
    client, suppliers, _, audit = make_client()
    with client:
        created = client.post("/api/v1/suppliers", json=supplier_payload(), headers=ADMIN)
        supplier_id = created.json()["supplier"]["id"]
        updated = client.put(
            f"/api/v1/suppliers/{supplier_id}",
            json={**supplier_payload(), "name": "Hygiene Gross Nord"},
            headers=ADMIN,
        )
        deactivated = client.delete(f"/api/v1/suppliers/{supplier_id}", headers=ADMIN)

    assert created.status_code == 201, created.text
    assert created.json()["supplier"]["primary_contact_name"] == "Erika Muster"
    assert suppliers.created is not None
    assert suppliers.created.country_code == "DE"
    assert updated.status_code == 200, updated.text
    assert suppliers.updated is not None
    assert suppliers.updated.name == "Hygiene Gross Nord"
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["supplier"]["is_active"] is False
    assert [entry["event_type"].value for entry in audit.entries] == [
        "SYSTEM_SUPPLIER_CREATED",
        "SYSTEM_SUPPLIER_UPDATED",
        "SYSTEM_SUPPLIER_DEACTIVATED",
    ]


def test_admin_can_create_and_deactivate_customers() -> None:
    client, _, customers, audit = make_client()
    payload = {
        "name": "Stadt Muster",
        "street": "Rathausplatz 1",
        "postal_code": "93047",
        "city": "Regensburg",
        "country_code": "de",
        "vat_id": "DE123456789",
        "leitweg_id": "991-03730-19",
        "payment_terms": "Payable within 14 days",
        "contact_email": "einkauf@musterstadt.de",
        "is_active": True,
    }
    with client:
        created = client.post("/api/v1/customers", json=payload, headers=ADMIN)
        customer_id = created.json()["customer"]["id"]
        deactivated = client.delete(f"/api/v1/customers/{customer_id}", headers=ADMIN)

    assert created.status_code == 201, created.text
    assert created.json()["customer"]["leitweg_id"] == "991-03730-19"
    assert customers.created is not None
    assert customers.created.country_code == "DE"
    assert deactivated.status_code == 200, deactivated.text
    assert [entry["event_type"].value for entry in audit.entries] == [
        "SYSTEM_CUSTOMER_CREATED",
        "SYSTEM_CUSTOMER_DEACTIVATED",
    ]
