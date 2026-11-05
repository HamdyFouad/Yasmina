"""Hardened ERPNext REST client.

The client only speaks the documented Frappe ``/api/resource`` API. Mutations are
idempotent where ERPNext offers a natural key:

* ``Item`` is looked up by ``item_code`` before create/update.
* ``Supplier`` and ``Customer`` are looked up by their display name.
* ``Quotation`` is looked up by a deterministic ``title`` containing the Yasmina
  offer number (ERPNext 15 has no customer-reference field on Quotation).

Transient transport failures, HTTP 429 and HTTP 5xx are retried with exponential
backoff. Validation errors (4xx) are never retried.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import quote

import httpx

from src.config import Settings
from src.models.enums import UnitOfMeasure
from src.models.schemas import CustomerOffer
from src.services.approval_service import verify_offer_signature

__all__ = [
    "ERPItem",
    "ERPNextClient",
    "ERPNextError",
    "ERPNextNotConfigured",
    "ERPQuotation",
    "ERPSyncResult",
    "erp_uom",
    "item_code_for_offer_line",
    "item_payload",
    "quotation_payload",
    "quotation_title",
    "supplier_payload",
]

_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_DUPLICATE_STATUSES = frozenset({409, 417})
_MAX_ITEM_CODE_LENGTH = 140


class ERPNextError(RuntimeError):
    """Raised when ERPNext rejects a request or cannot be reached."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ERPNextNotConfigured(ERPNextError):
    """Raised when ERPNext credentials have not been provisioned."""


@dataclass(frozen=True, slots=True)
class ERPItem:
    """Canonical item data sent to ERPNext."""

    item_code: str
    item_name: str
    unit: UnitOfMeasure
    description: str | None = None
    item_group: str | None = None


@dataclass(frozen=True, slots=True)
class ERPSyncResult:
    doctype: str
    name: str
    created: bool


@dataclass(frozen=True, slots=True)
class ERPQuotation:
    name: str
    title: str
    created: bool
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "quotation_name": self.name,
            "title": self.title,
            "created": self.created,
        }


def erp_uom(unit: UnitOfMeasure) -> str:
    """Map Yasmina units to the UOM names shipped with a standard ERPNext site."""

    return {
        UnitOfMeasure.STUECK: "Nos",
        UnitOfMeasure.PACK: "Box",
        UnitOfMeasure.KARTON: "Carton",
        UnitOfMeasure.PALETTE: "Pallet",
        UnitOfMeasure.ROLLE: "Roll",
        UnitOfMeasure.KILOGRAMM: "Kg",
        UnitOfMeasure.LITER: "Litre",
        UnitOfMeasure.METER: "Meter",
        UnitOfMeasure.QUADRATMETER: "Square Meter",
        UnitOfMeasure.NOT_APPLICABLE: "Nos",
    }[unit]


def _safe_code(value: str) -> str:
    normalized = "".join(char if char.isalnum() else "-" for char in value.upper())
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized.strip("-") or "ITEM"


def item_code_for_offer_line(offer_number: str, position_number: str) -> str:
    """Deterministic ERPNext item code for one offer line."""

    code = f"YAS-{_safe_code(offer_number)}-{_safe_code(position_number)}"
    return code[:_MAX_ITEM_CODE_LENGTH].rstrip("-")


def quotation_title(offer: CustomerOffer) -> str:
    """Deterministic title used as the ERPNext idempotency key for a quotation."""

    return f"Yasmina {offer.offer_number} | {offer.customer_name}"[:140]


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def item_payload(item: ERPItem) -> dict[str, Any]:
    return {
        "item_code": item.item_code[:_MAX_ITEM_CODE_LENGTH],
        "item_name": item.item_name[:140],
        "item_group": item.item_group,
        "stock_uom": erp_uom(item.unit),
        "description": item.description or item.item_name,
        "is_stock_item": False,
        "is_sales_item": True,
        "is_purchase_item": True,
    }


def supplier_payload(name: str, email: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"supplier_name": name, "supplier_type": "Company"}
    if email:
        payload["email_id"] = email
    return payload


def quotation_payload(
    offer: CustomerOffer,
    *,
    company: str,
    selling_price_list: str,
    quotation_series: str = "SAL-QTN-.YYYY.-",
) -> dict[str, Any]:
    """Map a signed ``CustomerOffer`` onto an ERPNext Quotation document."""

    approved_day: date = (
        offer.approved_at.astimezone(timezone.utc).date()
        if offer.approved_at is not None
        else offer.created_at.astimezone(timezone.utc).date()
    )
    items: list[dict[str, Any]] = []
    for line in sorted(offer.lines, key=lambda entry: entry.position_number):
        code = item_code_for_offer_line(offer.offer_number, line.position_number)
        items.append(
            {
                "item_code": code,
                "item_name": line.description[:140],
                "description": line.description,
                "qty": _decimal(line.quantity),
                "uom": erp_uom(line.unit),
                "rate": _decimal(line.unit_price_net),
                "amount": _decimal(line.total_net),
            }
        )

    payload: dict[str, Any] = {
        "naming_series": quotation_series,
        "quotation_to": "Customer",
        "party_name": offer.customer_name,
        "title": quotation_title(offer),
        "company": company,
        "transaction_date": approved_day.isoformat(),
        "valid_till": offer.valid_until.isoformat(),
        "order_type": "Sales",
        "currency": offer.currency.value,
        "conversion_rate": 1,
        "selling_price_list": selling_price_list,
        "price_list_currency": offer.currency.value,
        "plc_conversion_rate": 1,
        "items": items,
    }
    if offer.customer_contact_email:
        payload["contact_email"] = str(offer.customer_contact_email)
    if offer.payment_terms:
        payload["terms"] = offer.payment_terms
    return payload


class ERPNextClient:
    """Async, retrying ERPNext client with explicit idempotency semantics."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._sleep = sleep or asyncio.sleep

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.erpnext_base_url
            and self.settings.erpnext_api_key
            and self.settings.erpnext_api_key.get_secret_value()
            and self.settings.erpnext_api_secret
            and self.settings.erpnext_api_secret.get_secret_value()
        )

    @property
    def _base_url(self) -> str:
        return self.settings.erpnext_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise ERPNextNotConfigured("ERPNext API key/secret are not configured")
        assert self.settings.erpnext_api_key is not None
        assert self.settings.erpnext_api_secret is not None
        token = (
            f"{self.settings.erpnext_api_key.get_secret_value()}:"
            f"{self.settings.erpnext_api_secret.get_secret_value()}"
        )
        return {
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        payload: Mapping[str, Any] | None,
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(method, url, params=params, json=payload)
        async with httpx.AsyncClient(timeout=self.settings.erpnext_timeout_seconds) as client:
            return await client.request(method, url, params=params, json=payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = self._headers()
        url = f"{self._base_url}{path}"
        last_error: str | None = None

        for attempt in range(self.settings.erpnext_max_retries + 1):
            try:
                response = await self._send(method, url, params=params, payload=payload)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt >= self.settings.erpnext_max_retries:
                    raise ERPNextError(f"ERPNext request failed: {last_error}") from exc
                await self._sleep(self.settings.erpnext_retry_backoff_seconds * (2**attempt))
                continue

            if response.status_code in _RETRY_STATUSES and attempt < self.settings.erpnext_max_retries:
                delay = self._retry_delay(response, attempt)
                await self._sleep(delay)
                continue

            if not 200 <= response.status_code < 300:
                detail = response.text.strip()[:1000]
                raise ERPNextError(
                    f"ERPNext {method} {path} returned {response.status_code}: {detail}",
                    status_code=response.status_code,
                )

            if not response.content:
                return {}
            try:
                data = response.json()
            except ValueError as exc:
                raise ERPNextError("ERPNext returned a non-JSON response") from exc
            if not isinstance(data, dict):
                raise ERPNextError("ERPNext returned an unexpected JSON document")
            return data

        raise ERPNextError(f"ERPNext request failed after retries: {last_error or 'unknown error'}")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after.isdigit():
            return min(float(retry_after), 30.0)
        return self.settings.erpnext_retry_backoff_seconds * (2**attempt)

    async def _find_one(
        self,
        doctype: str,
        filters: Sequence[Sequence[str]],
    ) -> dict[str, Any] | None:
        params = {
            "filters": json.dumps(filters, separators=(",", ":")),
            "fields": json.dumps(["name"]),
            "limit_page_length": 1,
        }
        data = await self._request(
            "GET",
            f"/api/resource/{quote(doctype, safe='')}",
            params=params,
        )
        rows = data.get("data", [])
        if not isinstance(rows, list) or not rows:
            return None
        first = rows[0]
        return first if isinstance(first, dict) else None

    async def _create(self, doctype: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = await self._request(
            "POST",
            f"/api/resource/{quote(doctype, safe='')}",
            payload=payload,
        )
        created = data.get("data")
        if not isinstance(created, dict) or not created.get("name"):
            raise ERPNextError(f"ERPNext did not return a created {doctype} document")
        return created

    async def _update(
        self,
        doctype: str,
        name: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        data = await self._request(
            "PUT",
            f"/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}",
            payload=payload,
        )
        updated = data.get("data")
        return updated if isinstance(updated, dict) else {"name": name}

    async def sync_item(self, item: ERPItem) -> ERPSyncResult:
        """Create or update an ERPNext Item keyed by ``item_code``."""

        if not item.item_group:
            item = ERPItem(
                item_code=item.item_code,
                item_name=item.item_name,
                unit=item.unit,
                description=item.description,
                item_group=self.settings.erpnext_item_group,
            )
        existing = await self._find_one("Item", [["Item", "item_code", "=", item.item_code]])
        payload = item_payload(item)
        update_payload = {
            key: payload[key]
            for key in ("item_name", "item_group", "stock_uom", "description")
            if key in payload
        }
        if existing:
            name = str(existing["name"])
            await self._update("Item", name, update_payload)
            return ERPSyncResult("Item", name, False)

        try:
            created = await self._create("Item", payload)
        except ERPNextError as exc:
            if exc.status_code not in _DUPLICATE_STATUSES:
                raise
            existing = await self._find_one("Item", [["Item", "item_code", "=", item.item_code]])
            if existing is None:
                raise
            return ERPSyncResult("Item", str(existing["name"]), False)
        return ERPSyncResult("Item", str(created["name"]), True)

    async def sync_items(self, items: Sequence[ERPItem]) -> list[ERPSyncResult]:
        """Synchronize a batch of Items sequentially to respect ERPNext rate limits."""

        return [await self.sync_item(item) for item in items]

    async def sync_supplier(self, name: str, email: str | None = None) -> ERPSyncResult:
        """Create or update an ERPNext Supplier keyed by ``supplier_name``."""

        existing = await self._find_one("Supplier", [["Supplier", "supplier_name", "=", name]])
        payload = supplier_payload(name, email)
        if existing:
            supplier_id = str(existing["name"])
            await self._update("Supplier", supplier_id, payload)
            return ERPSyncResult("Supplier", supplier_id, False)
        try:
            created = await self._create("Supplier", payload)
        except ERPNextError as exc:
            if exc.status_code not in _DUPLICATE_STATUSES:
                raise
            existing = await self._find_one("Supplier", [["Supplier", "supplier_name", "=", name]])
            if existing is None:
                raise
            return ERPSyncResult("Supplier", str(existing["name"]), False)
        return ERPSyncResult("Supplier", str(created["name"]), True)

    async def sync_suppliers(
        self,
        suppliers: Sequence[tuple[str, str | None]],
    ) -> list[ERPSyncResult]:
        """Synchronize a batch of ``(name, email)`` supplier records."""

        return [await self.sync_supplier(name, email) for name, email in suppliers]

    async def ensure_customer(self, name: str, email: str | None = None) -> ERPSyncResult:
        """Resolve the quotation party, creating a Customer when necessary."""

        existing = await self._find_one("Customer", [["Customer", "customer_name", "=", name]])
        if existing:
            return ERPSyncResult("Customer", str(existing["name"]), False)
        payload: dict[str, Any] = {"customer_name": name, "customer_type": "Company"}
        if email:
            payload["email_id"] = email
        try:
            created = await self._create("Customer", payload)
        except ERPNextError as exc:
            if exc.status_code not in _DUPLICATE_STATUSES:
                raise
            existing = await self._find_one("Customer", [["Customer", "customer_name", "=", name]])
            if existing is None:
                raise
            return ERPSyncResult("Customer", str(existing["name"]), False)
        return ERPSyncResult("Customer", str(created["name"]), True)

    def _assert_human_approval(self, offer: CustomerOffer) -> None:
        if not (
            offer.ed25519_approval_signature
            and offer.approval_payload_hash
            and offer.approved_by
            and offer.approved_at
        ):
            raise ERPNextError("refusing to create an ERPNext Quotation without human approval")
        if not self.settings.ed25519_approval_public_key:
            raise ERPNextError("refusing to create an ERPNext Quotation without a configured approval key")
        if not verify_offer_signature(offer, self.settings.ed25519_approval_public_key):
            raise ERPNextError("refusing to create an ERPNext Quotation with an invalid approval signature")

    async def create_quotation(self, offer: CustomerOffer) -> ERPQuotation:
        """Create (or return) the ERPNext Quotation for an approved offer."""

        self._assert_human_approval(offer)
        title = quotation_title(offer)
        existing = await self._find_one("Quotation", [["Quotation", "title", "=", title]])
        if existing:
            return ERPQuotation(
                name=str(existing["name"]),
                title=title,
                created=False,
                payload={},
            )

        await self.ensure_customer(
            offer.customer_name,
            email=str(offer.customer_contact_email) if offer.customer_contact_email else None,
        )
        for line in sorted(offer.lines, key=lambda entry: entry.position_number):
            await self.sync_item(
                ERPItem(
                    item_code=item_code_for_offer_line(offer.offer_number, line.position_number),
                    item_name=line.description,
                    unit=line.unit,
                    description=line.description,
                    item_group=self.settings.erpnext_item_group,
                )
            )

        payload = quotation_payload(
            offer,
            company=self.settings.erpnext_company,
            selling_price_list=self.settings.erpnext_selling_price_list,
            quotation_series=self.settings.erpnext_quotation_series,
        )
        try:
            created = await self._create("Quotation", payload)
        except ERPNextError as exc:
            if exc.status_code not in _DUPLICATE_STATUSES:
                raise
            existing = await self._find_one("Quotation", [["Quotation", "title", "=", title]])
            if existing is None:
                raise
            return ERPQuotation(
                name=str(existing["name"]),
                title=title,
                created=False,
                payload=payload,
            )
        return ERPQuotation(
            name=str(created["name"]),
            title=title,
            created=True,
            payload=payload,
        )
