"""Procurement API tests with mocked repositories."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from src.api.deps import (
    get_audit_service,
    get_procurement_repository,
    get_session,
)
from src.api.security import Role, create_access_token
from src.config import Settings
from src.main import create_app
from src.repositories.procurement_repository import (
    ProcurementValidationError,
    PurchaseOrderLineView,
    PurchaseOrderView,
    SupplierInvoiceLineView,
    SupplierInvoiceView,
)
from src.services.procurement_service import (
    MarginSummary,
    PurchaseOrderTransitionError,
)

SETTINGS = Settings(
    _env_file=None,
    database_url=None,
    app_env="test",
    jwt_secret_key="procurement-jwt-secret-with-32-bytes",
    api_admin_password="test-admin-password",
)
ADMIN = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'admin', [Role.ADMIN])}"}
SALES = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'sales', [Role.SALES])}"}
VIEWER = {"Authorization": f"Bearer {create_access_token(SETTINGS, 'viewer', [Role.VIEWER])}"}
NOW = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
PO_ID = uuid4()
INVOICE_ID = uuid4()
OFFER_ID = uuid4()


class FakeSession:
    async def get(self, model: Any, key: Any) -> None:
        return None


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def make_po() -> PurchaseOrderView:
    line = PurchaseOrderLineView(
        id=uuid4(),
        purchase_order_id=PO_ID,
        supplier_quote_line_id=uuid4(),
        line_number=1,
        description="Nitrilhandschuhe",
        tender_item_id=uuid4(),
        quantity=Decimal("10.0000"),
        unit="KARTON",
        unit_price_net=Decimal("20.0000"),
        total_net=Decimal("200.00"),
        received_quantity=Decimal("0.0000"),
    )
    return PurchaseOrderView(
        id=PO_ID,
        po_number="PO-2026-ABCD1234",
        supplier_id=uuid4(),
        supplier_name="Hygiene Gross",
        supplier_contact_email="rfq@hygiene-gross.de",
        tender_id=uuid4(),
        supplier_quote_id=uuid4(),
        customer_offer_id=OFFER_ID,
        status="DRAFT",
        currency="EUR",
        order_date=date(2026, 2, 1),
        expected_delivery_date=date(2026, 2, 10),
        sent_at=None,
        confirmed_at=None,
        delivered_at=None,
        cancelled_at=None,
        total_net=Decimal("200.00"),
        notes=None,
        requested_by="admin",
        lines=(line,),
        created_at=NOW,
        updated_at=NOW,
    )


def make_invoice() -> SupplierInvoiceView:
    line = SupplierInvoiceLineView(
        id=uuid4(),
        supplier_invoice_id=INVOICE_ID,
        purchase_order_line_id=make_po().lines[0].id,
        description="Nitrilhandschuhe",
        quantity=Decimal("10.0000"),
        unit="KARTON",
        unit_price_net=Decimal("21.0000"),
        total_net=Decimal("210.00"),
    )
    return SupplierInvoiceView(
        id=INVOICE_ID,
        purchase_order_id=PO_ID,
        po_number="PO-2026-ABCD1234",
        supplier_id=uuid4(),
        supplier_name="Hygiene Gross",
        invoice_number="INV-SUP-1",
        invoice_date=date(2026, 2, 11),
        delivery_date=date(2026, 2, 12),
        due_date=date(2026, 3, 1),
        currency="EUR",
        total_net=Decimal("210.00"),
        total_vat=Decimal("39.90"),
        total_gross=Decimal("249.90"),
        status="EXCEPTION",
        exception_codes=("TOTAL_MISMATCH", "DELIVERY_LATE"),
        variance_net=Decimal("10.00"),
        match_details={"status": "EXCEPTION"},
        matched_at=NOW,
        notes=None,
        received_by="admin",
        lines=(line,),
        created_at=NOW,
        updated_at=NOW,
    )


def make_margin() -> MarginSummary:
    return MarginSummary(
        offer_id=OFFER_ID,
        offer_number="E2E-100",
        customer_name="Stadt Muster",
        currency="EUR",
        quoted_revenue_net=Decimal("1000.00"),
        quoted_cost_net=Decimal("600.00"),
        quoted_margin_net=Decimal("400.00"),
        quoted_margin_rate=Decimal("0.4000"),
        actual_cost_net=Decimal("650.00"),
        actual_margin_net=Decimal("350.00"),
        actual_margin_rate=Decimal("0.3500"),
        margin_variance_net=Decimal("-50.00"),
        margin_variance_rate=Decimal("-0.0500"),
        invoice_count=1,
        purchase_order_count=1,
        exception_count=0,
        exception_codes=(),
        coverage_rate=Decimal("1.0833"),
    )


class FakeProcurement:
    def __init__(self) -> None:
        self.po = make_po()
        self.invoice = make_invoice()
        self.margin = make_margin()
        self.created_po = False
        self.created_invoice = False
        self.transitioned_to: str | None = None

    async def list_purchase_orders(self, **kwargs: Any) -> list[PurchaseOrderView]:
        return [self.po]

    async def count_purchase_orders(self, **kwargs: Any) -> int:
        return 1

    async def get_purchase_order(self, purchase_order_id: UUID) -> PurchaseOrderView | None:
        return self.po if purchase_order_id == PO_ID else None

    async def create_purchase_order_from_quote(self, quote_id: UUID, **kwargs: Any) -> PurchaseOrderView:
        self.created_po = True
        return self.po

    async def transition_purchase_order(self, purchase_order_id: UUID, target: Any) -> PurchaseOrderView:
        self.transitioned_to = str(target)
        return self.po

    async def list_supplier_invoices(self, **kwargs: Any) -> list[SupplierInvoiceView]:
        return [self.invoice]

    async def count_supplier_invoices(self, **kwargs: Any) -> int:
        return 1

    async def get_supplier_invoice(self, invoice_id: UUID) -> SupplierInvoiceView | None:
        return self.invoice if invoice_id == INVOICE_ID else None

    async def create_supplier_invoice(self, payload: Any, **kwargs: Any) -> SupplierInvoiceView:
        self.created_invoice = True
        return self.invoice

    async def rematch_supplier_invoice(self, invoice_id: UUID) -> SupplierInvoiceView:
        return self.invoice

    async def list_margin_summaries(self, **kwargs: Any) -> list[MarginSummary]:
        return [self.margin]

    async def get_margin_for_offer(self, offer_id: UUID) -> MarginSummary | None:
        return self.margin if offer_id == OFFER_ID else None


class FailingProcurement(FakeProcurement):
    async def get_purchase_order(self, purchase_order_id: UUID) -> PurchaseOrderView | None:
        return self.po

    async def transition_purchase_order(self, purchase_order_id: UUID, target: Any) -> PurchaseOrderView:
        raise PurchaseOrderTransitionError("cannot transition purchase order from DRAFT to DELIVERED")

    async def create_purchase_order_from_quote(self, quote_id: UUID, **kwargs: Any) -> PurchaseOrderView:
        raise ProcurementValidationError("supplier quote must be accepted before creating a PO")


def make_client(
    repository: FakeProcurement | None = None,
) -> tuple[TestClient, FakeProcurement, FakeAudit]:
    app = create_app(SETTINGS, run_startup_checks=False)
    repo = repository or FakeProcurement()
    audit = FakeAudit()
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_procurement_repository] = lambda: repo
    app.dependency_overrides[get_audit_service] = lambda: audit
    return TestClient(app), repo, audit


def po_payload() -> dict[str, Any]:
    return {
        "supplier_quote_id": str(uuid4()),
        "expected_delivery_date": "2026-02-10",
        "notes": "Call before delivery",
    }


def invoice_payload() -> dict[str, Any]:
    return {
        "purchase_order_id": str(PO_ID),
        "invoice_number": "INV-SUP-1",
        "invoice_date": "2026-02-11",
        "delivery_date": "2026-02-12",
        "total_net": "210.00",
        "vat_rate": "0.1900",
        "lines": [
            {
                "purchase_order_line_id": str(make_po().lines[0].id),
                "quantity": "10.0000",
                "unit_price_net": "21.0000",
            }
        ],
    }


def test_procurement_reads_are_role_scoped_and_writes_reject_viewers() -> None:
    client, _, _ = make_client()
    with client:
        orders = client.get("/api/v1/procurement/purchase-orders", headers=SALES)
        invoices = client.get("/api/v1/procurement/supplier-invoices", headers=VIEWER)
        forbidden = client.post("/api/v1/procurement/purchase-orders", json=po_payload(), headers=VIEWER)

    assert orders.status_code == 200, orders.text
    assert orders.json()["count"] == 1
    assert invoices.status_code == 200, invoices.text
    assert forbidden.status_code == 403


def test_admin_creates_po_and_invoice_and_reads_margin() -> None:
    client, repo, audit = make_client()
    with client:
        created = client.post("/api/v1/procurement/purchase-orders", json=po_payload(), headers=ADMIN)
        invoice = client.post("/api/v1/procurement/supplier-invoices", json=invoice_payload(), headers=ADMIN)
        margins = client.get("/api/v1/procurement/margins", headers=ADMIN)
        detail = client.get(f"/api/v1/procurement/margins/{OFFER_ID}", headers=ADMIN)

    assert created.status_code == 201, created.text
    assert created.json()["purchase_order"]["po_number"] == "PO-2026-ABCD1234"
    assert repo.created_po is True
    assert invoice.status_code == 201, invoice.text
    assert invoice.json()["supplier_invoice"]["status"] == "EXCEPTION"
    assert repo.created_invoice is True
    assert margins.status_code == 200, margins.text
    assert margins.json()["margins"][0]["actual_margin_rate"] == "0.3500"
    assert detail.status_code == 200, detail.text
    assert detail.json()["margin"]["quoted_margin_rate"] == "0.4000"
    assert [entry["event_type"].value for entry in audit.entries] == [
        "PURCHASE_ORDER_CREATED",
        "SUPPLIER_INVOICE_RECORDED",
    ]


def test_po_transition_conflict_returns_409() -> None:
    client, _, audit = make_client(FailingProcurement())
    with client:
        response = client.post(
            f"/api/v1/procurement/purchase-orders/{PO_ID}/status",
            json={"status": "DELIVERED"},
            headers=ADMIN,
        )

    assert response.status_code == 409, response.text
    assert "cannot transition" in response.json()["detail"]
    assert audit.entries == []


def test_po_creation_rejects_unaccepted_quote() -> None:
    client, _, _ = make_client(FailingProcurement())
    with client:
        response = client.post("/api/v1/procurement/purchase-orders", json=po_payload(), headers=ADMIN)

    assert response.status_code == 422, response.text
    assert "accepted" in response.json()["detail"]
