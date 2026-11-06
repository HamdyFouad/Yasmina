"""ERPNext client tests: retries, idempotent sync and Quotation mapping."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from src.config import Settings
from src.integrations.erpnext_client import (
    ERPItem,
    ERPNextClient,
    ERPNextError,
    erp_uom,
    item_code_for_offer_line,
    quotation_payload,
    quotation_title,
    supplier_payload,
)
from src.models.enums import CustomerOfferStatus, UnitOfMeasure
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.services.approval_service import generate_keypair, sign_offer

PRIVATE_KEY, PUBLIC_KEY = generate_keypair()
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def make_settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "_env_file": None,
        "database_url": None,
        "erpnext_base_url": "https://erp.example.test",
        "erpnext_api_key": "api-key",
        "erpnext_api_secret": "api-secret",
        "erpnext_retry_backoff_seconds": 0,
        "ed25519_approval_public_key": PUBLIC_KEY,
    }
    data.update(overrides)
    return Settings(**data)


def make_offer(*, signed: bool = True) -> CustomerOffer:
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
    offer = CustomerOffer(
        offer_number="AN-2026-0001",
        customer_name="Stadt Muster",
        status=CustomerOfferStatus.DRAFT,
        lines=[line],
        margin_rate=margin,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
    )
    if signed:
        offer = sign_offer(offer, PRIVATE_KEY, approver="C. Mueller", approved_at=NOW)
    return offer


def test_uom_and_item_code_mapping() -> None:
    assert erp_uom(UnitOfMeasure.KARTON) == "Carton"
    assert erp_uom(UnitOfMeasure.STUECK) == "Nos"
    assert item_code_for_offer_line("AN-2026/1", "1.1") == "YAS-AN-2026-1-1-1"


def test_supplier_payload_uses_erpnext_field_names() -> None:
    assert supplier_payload("Hygiene Gross GmbH", "sales@example.com") == {
        "supplier_name": "Hygiene Gross GmbH",
        "supplier_type": "Company",
        "email_id": "sales@example.com",
    }


def test_quotation_payload_maps_decimals_and_customer() -> None:
    offer = make_offer()
    payload = quotation_payload(
        offer,
        company="Yasmina",
        selling_price_list="Standard Selling",
    )

    assert payload["quotation_to"] == "Customer"
    assert payload["party_name"] == "Stadt Muster"
    assert payload["title"] == quotation_title(offer)
    assert payload["company"] == "Yasmina"
    assert payload["currency"] == "EUR"
    assert payload["transaction_date"] == "2026-01-15"
    assert payload["valid_till"] == "2026-12-31"
    assert payload["items"] == [
        {
            "item_code": "YAS-AN-2026-0001-1-1",
            "item_name": "Nitrilhandschuhe blau Gr. M",
            "description": "Nitrilhandschuhe blau Gr. M",
            "qty": "10.0000",
            "uom": "Carton",
            "rate": "11.5000",
            "amount": "115.00",
        }
    ]


async def test_sync_item_retries_transient_failures() -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if len(calls) == 1:
            return httpx.Response(503, text="maintenance")
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": {"name": "ITEM-1"}})

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ERPNextClient(make_settings(), client=http, sleep=sleep)
        result = await client.sync_item(
            ERPItem(item_code="ITEM-1", item_name="Gloves", unit=UnitOfMeasure.KARTON)
        )

    assert result.created is True
    assert result.name == "ITEM-1"
    assert calls == ["GET /api/resource/Item", "GET /api/resource/Item", "POST /api/resource/Item"]
    assert sleeps == [0]


async def test_validation_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad item")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ERPNextClient(make_settings(), client=http, sleep=_noop_sleep)
        with pytest.raises(ERPNextError, match="400"):
            await client.sync_item(ERPItem(item_code="ITEM-1", item_name="Gloves", unit=UnitOfMeasure.KARTON))

    assert calls == 1


async def test_item_and_supplier_sync_are_idempotent() -> None:
    items: dict[str, dict[str, Any]] = {}
    suppliers: dict[str, dict[str, Any]] = {}
    writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        filters = json.loads(params.get("filters", "[]"))
        value = filters[0][3] if filters else None
        if path.endswith("/Item"):
            if request.method == "GET":
                row = items.get(value)
                return httpx.Response(200, json={"data": [{"name": row["name"]}]} if row else {"data": []})
            writes.append("POST Item")
            body = json.loads(request.content)
            items[body["item_code"]] = {"name": "ITEM-1", **body}
            return httpx.Response(200, json={"data": {"name": "ITEM-1"}})
        if path.endswith("/Item/ITEM-1"):
            writes.append("PUT Item")
            items[value] = {"name": "ITEM-1", **json.loads(request.content)}
            return httpx.Response(200, json={"data": {"name": "ITEM-1"}})
        if path.endswith("/Supplier"):
            if request.method == "GET":
                row = suppliers.get(value)
                return httpx.Response(200, json={"data": [{"name": row["name"]}]} if row else {"data": []})
            writes.append("POST Supplier")
            body = json.loads(request.content)
            suppliers[body["supplier_name"]] = {"name": "SUP-1", **body}
            return httpx.Response(200, json={"data": {"name": "SUP-1"}})
        if path.endswith("/Supplier/SUP-1"):
            writes.append("PUT Supplier")
            suppliers[value] = {"name": "SUP-1", **json.loads(request.content)}
            return httpx.Response(200, json={"data": {"name": "SUP-1"}})
        raise AssertionError(f"unexpected request {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ERPNextClient(make_settings(), client=http, sleep=_noop_sleep)
        item = ERPItem(item_code="ITEM-1", item_name="Gloves", unit=UnitOfMeasure.KARTON)
        first = await client.sync_item(item)
        second = await client.sync_item(item)
        supplier_first = await client.sync_supplier("Hygiene Gross GmbH", email="sales@example.com")
        supplier_second = await client.sync_supplier("Hygiene Gross GmbH", email="sales@example.com")

    assert (first.created, second.created) == (True, False)
    assert (supplier_first.created, supplier_second.created) == (True, False)
    assert writes == ["POST Item", "PUT Item", "POST Supplier", "PUT Supplier"]


async def test_quotation_creation_returns_existing_document_by_title() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/Quotation")
        return httpx.Response(200, json={"data": [{"name": "QTN-OLD"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ERPNextClient(make_settings(), client=http, sleep=_noop_sleep)
        quotation = await client.create_quotation(make_offer())

    assert quotation.name == "QTN-OLD"
    assert quotation.created is False
    assert len(requests) == 1
    assert json.loads(requests[0].url.params["filters"]) == [
        ["Quotation", "title", "=", quotation_title(make_offer())]
    ]


async def test_quotation_creation_syncs_customer_and_items() -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, path, body))
        if path.endswith("/Quotation") and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path.endswith("/Customer") and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path.endswith("/Customer"):
            return httpx.Response(200, json={"data": {"name": "CUST-1"}})
        if path.endswith("/Item") and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path.endswith("/Item"):
            return httpx.Response(200, json={"data": {"name": body["item_code"]}})
        if path.endswith("/Quotation") and request.method == "POST":
            return httpx.Response(200, json={"data": {"name": "QTN-0001"}})
        raise AssertionError(f"unexpected request {request.method} {path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ERPNextClient(make_settings(), client=http, sleep=_noop_sleep)
        quotation = await client.create_quotation(make_offer())

    assert quotation.created is True
    assert quotation.name == "QTN-0001"
    quotation_body = next(
        body for method, path, body in requests if method == "POST" and path.endswith("/Quotation")
    )
    assert quotation_body["party_name"] == "Stadt Muster"
    assert quotation_body["items"][0]["qty"] == "10.0000"


async def test_quotation_requires_human_approval() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as http:
        client = ERPNextClient(make_settings(), client=http, sleep=_noop_sleep)
        with pytest.raises(ERPNextError, match="without human approval"):
            await client.create_quotation(make_offer(signed=False))


async def test_quotation_rejects_tampered_signature() -> None:
    tampered = make_offer().model_copy(update={"customer_name": "Evil GmbH"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as http:
        client = ERPNextClient(make_settings(), client=http, sleep=_noop_sleep)
        with pytest.raises(ERPNextError, match="invalid approval signature"):
            await client.create_quotation(tampered)


async def _noop_sleep(delay: float) -> None:
    return None
