"""n8n HMAC client tests: signing, retries and callback rejection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest

from src.config import Settings
from src.integrations.n8n_client import (
    IDEMPOTENCY_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    N8nClient,
    N8nConfigurationError,
    N8nSignatureError,
    build_rfq_payload,
    sign_body,
    verify_callback_signature,
)
from src.models.enums import ProcurementProcedure, TenderSourceFormat, UnitOfMeasure
from src.models.schemas import Tender, TenderItem

SECRET = "test-n8n-hmac-secret"
NOW = 1_800_000_000


def make_settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "_env_file": None,
        "database_url": None,
        "n8n_webhook_base_url": "https://n8n.example.test",
        "n8n_webhook_hmac_secret": SECRET,
        "n8n_callback_max_age_seconds": 300,
        "n8n_retry_backoff_seconds": 0,
    }
    data.update(overrides)
    return Settings(**data)


def make_tender() -> Tender:
    item = TenderItem(
        position_number="1.1",
        short_text="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
    )
    return Tender(
        tender_number="T-1",
        title="Rahmenvertrag",
        client_name="Stadt Muster",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.GAEB_X83,
        submission_deadline=datetime(2026, 2, 1, tzinfo=timezone.utc),
        items=[item],
    )


def test_signature_roundtrip_and_replay_window() -> None:
    body = b'{"event":"rfq"}'
    signature = sign_body(SECRET, body, NOW)

    assert signature.startswith("sha256=")
    assert verify_callback_signature(SECRET, body, NOW, signature, max_age_seconds=300, now=NOW) == NOW
    with pytest.raises(N8nSignatureError, match="replay window"):
        verify_callback_signature(SECRET, body, NOW, signature, max_age_seconds=300, now=NOW + 301)
    with pytest.raises(N8nSignatureError, match="invalid"):
        verify_callback_signature(SECRET, body + b"x", NOW, signature, max_age_seconds=300, now=NOW)


async def test_dispatch_signs_exact_body_and_idempotency_key() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"accepted": True})

    settings = make_settings()
    body = {"event_type": "rfq.dispatch", "value": 1}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = N8nClient(settings, client=http, clock=lambda: float(NOW))
        result = await client.dispatch_rfq(body, idempotency_key="rfq-key-0001")

    assert result.status_code == 200
    assert result.response == {"accepted": True}
    request = captured[0]
    assert str(request.url) == "https://n8n.example.test/webhook/yasmina/rfq"
    assert request.headers[IDEMPOTENCY_HEADER] == "rfq-key-0001"
    assert request.headers[TIMESTAMP_HEADER] == str(NOW)
    signature = request.headers[SIGNATURE_HEADER]
    verify_callback_signature(
        SECRET,
        request.content,
        request.headers[TIMESTAMP_HEADER],
        signature,
        max_age_seconds=300,
        now=NOW,
    )
    assert json.loads(request.content) == body


async def test_dispatch_retries_transient_status() -> None:
    statuses = [503, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(statuses.pop(0), json={"ok": True})

    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = N8nClient(make_settings(), client=http, sleep=sleep, clock=lambda: float(NOW))
        result = await client.dispatch_rfq({"event": "rfq"}, idempotency_key="rfq-key-0002")

    assert result.status_code == 200
    assert sleeps == [0]


def test_callback_verification_is_case_insensitive_and_requires_secret() -> None:
    body = b'{"quotes":[]}'
    signature = sign_body(SECRET, body, NOW)
    headers = {
        "x-yasmina-signature": signature,
        "x-yasmina-timestamp": str(NOW),
    }
    client = N8nClient(make_settings(), clock=lambda: float(NOW))

    assert client.verify_callback(body, headers) == NOW

    unconfigured = N8nClient(
        make_settings(n8n_webhook_hmac_secret=None),
        clock=lambda: float(NOW),
    )
    assert unconfigured.configured is False
    with pytest.raises(N8nConfigurationError):
        unconfigured.verify_callback(body, headers)


def test_build_rfq_payload_contains_items_and_callback() -> None:
    payload = build_rfq_payload(
        event_id="event-1",
        tender=make_tender(),
        suppliers=[{"name": "Hygiene Gross GmbH", "email": "sales@example.com"}],
        callback_url="https://api.example.test/callback",
        idempotency_key="rfq-key-0003",
        dispatched_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
    )

    assert payload["event_type"] == "rfq.dispatch"
    assert payload["tender"]["items"][0]["quantity"] == "10.0000"
    assert payload["suppliers"][0]["email"] == "sales@example.com"
    assert payload["callback_url"] == "https://api.example.test/callback"
