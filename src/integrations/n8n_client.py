"""HMAC-authenticated n8n webhook client.

Outbound RFQ requests and inbound supplier-quote callbacks share one canonical
signature scheme:

``HMAC-SHA256(secret, "<unix-timestamp>." + raw_body)``

The digest is sent as ``X-Yasmina-Signature: sha256=<hex>`` together with
``X-Yasmina-Timestamp``. Inbound requests older (or further in the future) than
``N8N_CALLBACK_MAX_AGE_SECONDS`` are rejected to prevent replay.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

import httpx

from src.config import Settings
from src.models.schemas import Tender

__all__ = [
    "N8nClient",
    "N8nConfigurationError",
    "N8nDispatchResult",
    "N8nError",
    "N8nSignatureError",
    "build_rfq_payload",
    "sign_body",
    "verify_callback_signature",
]

SIGNATURE_HEADER = "X-Yasmina-Signature"
TIMESTAMP_HEADER = "X-Yasmina-Timestamp"
IDEMPOTENCY_HEADER = "Idempotency-Key"
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class N8nError(RuntimeError):
    """Raised when an n8n webhook cannot be signed, sent or verified."""


class N8nConfigurationError(N8nError):
    """Raised when the shared HMAC secret is missing."""


class N8nSignatureError(N8nError):
    """Raised when an inbound signature is missing, stale or invalid."""


@dataclass(frozen=True, slots=True)
class N8nDispatchResult:
    status_code: int
    idempotency_key: str
    response: dict[str, Any] | str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "idempotency_key": self.idempotency_key,
            "response": self.response,
        }


def _secret_value(secret: str | Any) -> str:
    value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    if not value:
        raise N8nConfigurationError("N8N_WEBHOOK_HMAC_SECRET is not configured")
    return value


def sign_body(secret: str | Any, body: bytes, timestamp: int | str) -> str:
    """Return the canonical ``sha256=<hex>`` signature for a raw body."""

    key = _secret_value(secret).encode("utf-8")
    message = f"{timestamp}.".encode("ascii") + body
    return "sha256=" + hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_callback_signature(
    secret: str | Any,
    body: bytes,
    timestamp: int | str,
    signature: str,
    *,
    max_age_seconds: int,
    now: float | None = None,
) -> int:
    """Validate a callback signature and replay window.

    Returns the parsed Unix timestamp. Raises :class:`N8nSignatureError` for a
    missing/malformed signature, a stale timestamp or a digest mismatch.
    """

    if not signature:
        raise N8nSignatureError("missing X-Yasmina-Signature header")
    try:
        timestamp_value = int(str(timestamp).strip())
    except (TypeError, ValueError) as exc:
        raise N8nSignatureError("missing or invalid X-Yasmina-Timestamp header") from exc

    current = time.time() if now is None else now
    if abs(current - timestamp_value) > max_age_seconds:
        raise N8nSignatureError("callback timestamp is outside the allowed replay window")

    provided = signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[7:]
    if len(provided) != 64:
        raise N8nSignatureError("malformed callback signature")
    expected = sign_body(secret, body, timestamp_value)
    if not hmac.compare_digest(expected[7:], provided.lower()):
        raise N8nSignatureError("invalid callback signature")
    return timestamp_value


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def build_rfq_payload(
    *,
    event_id: str,
    tender: Tender,
    suppliers: Sequence[Mapping[str, Any]],
    callback_url: str,
    idempotency_key: str,
    response_deadline: datetime | None = None,
    notes: str | None = None,
    dispatched_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the versioned RFQ envelope sent to n8n."""

    dispatched = dispatched_at or datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": "rfq.dispatch",
        "idempotency_key": idempotency_key,
        "dispatched_at": dispatched.astimezone(timezone.utc).isoformat(),
        "callback_url": callback_url,
        "tender": {
            "tender_id": str(tender.id),
            "tender_number": tender.tender_number,
            "title": tender.title,
            "description": tender.description,
            "client_name": tender.client_name,
            "submission_deadline": tender.submission_deadline.isoformat(),
            "currency": tender.currency.value,
            "items": [
                {
                    "tender_item_id": str(item.id),
                    "position_number": item.position_number,
                    "lot": item.lot,
                    "description": item.short_text,
                    "quantity": format(item.quantity, "f"),
                    "unit": item.unit.value,
                    "delivery_location": item.delivery_location,
                    "requested_delivery_date": (
                        item.requested_delivery_date.isoformat()
                        if item.requested_delivery_date is not None
                        else None
                    ),
                }
                for item in sorted(tender.items, key=lambda entry: entry.position_number)
            ],
        },
        "suppliers": [dict(supplier) for supplier in suppliers],
    }
    if response_deadline is not None:
        payload["response_deadline"] = response_deadline.astimezone(timezone.utc).isoformat()
    if notes:
        payload["notes"] = notes
    return payload


class N8nClient:
    """Async n8n webhook client with HMAC signing and transient retries."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or time.time

    @property
    def configured(self) -> bool:
        secret = self.settings.n8n_webhook_hmac_secret
        return bool(
            self.settings.n8n_webhook_base_url
            and secret is not None
            and secret.get_secret_value()
        )

    @property
    def webhook_url(self) -> str:
        return (
            self.settings.n8n_webhook_base_url.rstrip("/")
            + "/"
            + self.settings.n8n_rfq_webhook_path.lstrip("/")
        )

    @property
    def callback_url(self) -> str:
        return self.settings.n8n_callback_url

    def _headers(self, body: bytes, idempotency_key: str) -> dict[str, str]:
        secret = self.settings.n8n_webhook_hmac_secret
        if secret is None:
            raise N8nConfigurationError("N8N_WEBHOOK_HMAC_SECRET is not configured")
        timestamp = int(self._clock())
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            SIGNATURE_HEADER: sign_body(secret, body, timestamp),
            TIMESTAMP_HEADER: str(timestamp),
            IDEMPOTENCY_HEADER: idempotency_key,
            "User-Agent": "yasmina-integration/1.0",
        }

    async def _send(self, url: str, *, content: bytes, headers: Mapping[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, content=content, headers=headers)
        async with httpx.AsyncClient(timeout=self.settings.n8n_timeout_seconds) as client:
            return await client.post(url, content=content, headers=headers)

    async def dispatch_rfq(
        self,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> N8nDispatchResult:
        """POST a signed RFQ envelope to n8n."""

        if not self.configured:
            raise N8nConfigurationError("n8n webhook URL or HMAC secret is not configured")
        body = _canonical_json(payload)
        headers = self._headers(body, idempotency_key)
        last_error: str | None = None

        for attempt in range(self.settings.n8n_max_retries + 1):
            try:
                response = await self._send(self.webhook_url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt >= self.settings.n8n_max_retries:
                    raise N8nError(f"n8n webhook request failed: {last_error}") from exc
                await self._sleep(self.settings.n8n_retry_backoff_seconds * (2**attempt))
                continue

            if response.status_code in _RETRY_STATUSES and attempt < self.settings.n8n_max_retries:
                retry_after = response.headers.get("Retry-After", "").strip()
                delay = (
                    min(float(retry_after), 30.0)
                    if retry_after.isdigit()
                    else self.settings.n8n_retry_backoff_seconds * (2**attempt)
                )
                await self._sleep(delay)
                continue

            if not 200 <= response.status_code < 300:
                raise N8nError(
                    f"n8n webhook returned {response.status_code}: {response.text.strip()[:1000]}"
                )
            return N8nDispatchResult(
                status_code=response.status_code,
                idempotency_key=idempotency_key,
                response=self._response_payload(response),
            )

        raise N8nError(f"n8n webhook failed after retries: {last_error or 'unknown error'}")

    def _response_payload(self, response: httpx.Response) -> dict[str, Any] | str:
        if not response.content:
            return {}
        try:
            parsed = response.json()
        except ValueError:
            return response.text[:2000]
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def verify_callback(
        self,
        body: bytes,
        headers: Mapping[str, str],
        *,
        now: float | None = None,
    ) -> int:
        """Verify an inbound n8n callback and return its timestamp."""

        secret = self.settings.n8n_webhook_hmac_secret
        if secret is None:
            raise N8nConfigurationError("N8N_WEBHOOK_HMAC_SECRET is not configured")
        signature = _header(headers, SIGNATURE_HEADER)
        timestamp = _header(headers, TIMESTAMP_HEADER)
        return verify_callback_signature(
            secret,
            body,
            timestamp,
            signature,
            max_age_seconds=self.settings.n8n_callback_max_age_seconds,
            now=now if now is not None else self._clock(),
        )
