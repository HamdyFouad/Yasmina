"""Telegram alerting for failed scheduled runs and dead-letter ingestion jobs.

Alerts use the Bot API ``sendMessage`` endpoint and are deliberately
best-effort: a delivery failure is logged and never breaks the worker. The
bot token is read from ``TELEGRAM_BOT_TOKEN`` and the destination chat from
``TELEGRAM_APPROVAL_CHAT_ID``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

import httpx

from src.config import Settings

__all__ = [
    "TelegramAlertResult",
    "TelegramConfigurationError",
    "TelegramError",
    "TelegramNotifier",
]

logger = logging.getLogger("yasmina.integrations.telegram")

_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class TelegramError(RuntimeError):
    """Raised when a Telegram alert cannot be delivered."""


class TelegramConfigurationError(TelegramError):
    """Raised when the bot token or approval chat id is missing."""


@dataclass(frozen=True, slots=True)
class TelegramAlertResult:
    chat_id: str
    message_id: int | None

    def as_dict(self) -> dict[str, Any]:
        return {"chat_id": self.chat_id, "message_id": self.message_id}


class TelegramNotifier:
    """Best-effort Telegram Bot API notifier with transient retries."""

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
        token = self.settings.telegram_bot_token
        chat_id = (self.settings.telegram_approval_chat_id or "").strip()
        return bool(
            self.settings.telegram_alerts_enabled
            and token is not None
            and token.get_secret_value().strip()
            and chat_id
        )

    @property
    def chat_id(self) -> str:
        return (self.settings.telegram_approval_chat_id or "").strip()

    @property
    def api_url(self) -> str:
        token = self.settings.telegram_bot_token
        if token is None or not token.get_secret_value().strip():
            raise TelegramConfigurationError("TELEGRAM_BOT_TOKEN is not configured")
        return f"https://api.telegram.org/bot{token.get_secret_value().strip()}/sendMessage"

    async def _send(self, url: str, payload: Mapping[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=dict(payload))
        async with httpx.AsyncClient(timeout=self.settings.telegram_timeout_seconds) as client:
            return await client.post(url, json=dict(payload))

    async def send_alert(self, text: str, *, parse_mode: str | None = None) -> TelegramAlertResult:
        """Send one alert and raise :class:`TelegramError` when delivery fails."""

        if not self.configured:
            raise TelegramConfigurationError(
                "Telegram alerts are disabled or TELEGRAM_BOT_TOKEN/TELEGRAM_APPROVAL_CHAT_ID is missing"
            )
        message = text.strip()
        if not message:
            raise TelegramError("Telegram alert text must not be empty")
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message[:4000],
            "disable_web_page_preview": True,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        last_error: str | None = None
        token = self.settings.telegram_bot_token.get_secret_value().strip()
        for attempt in range(self.settings.telegram_max_retries + 1):
            try:
                response = await self._send(self.api_url, payload)
            except httpx.HTTPError as exc:
                last_error = _redact(str(exc), token)
                if attempt >= self.settings.telegram_max_retries:
                    raise TelegramError(f"Telegram alert request failed: {last_error}") from exc
                await self._sleep(self.settings.telegram_retry_backoff_seconds * (2**attempt))
                continue

            if response.status_code in _RETRY_STATUSES and attempt < self.settings.telegram_max_retries:
                retry_after = response.headers.get("Retry-After", "").strip()
                delay = (
                    min(float(retry_after), 30.0)
                    if retry_after.isdigit()
                    else self.settings.telegram_retry_backoff_seconds * (2**attempt)
                )
                await self._sleep(delay)
                continue
            if not 200 <= response.status_code < 300:
                raise TelegramError(
                    f"Telegram API returned {response.status_code}: {_description(response)}"
                )
            return TelegramAlertResult(chat_id=self.chat_id, message_id=_message_id(response))

        raise TelegramError(f"Telegram alert failed after retries: {last_error or 'unknown error'}")

    async def notify(self, text: str) -> bool:
        """Best-effort wrapper that logs (but never raises) delivery failures."""

        try:
            result = await self.send_alert(text)
        except TelegramError as exc:
            logger.warning("telegram alert not delivered: %s", exc)
            return False
        logger.info("telegram alert sent chat=%s message_id=%s", result.chat_id, result.message_id)
        return True


def _description(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:500]
    if isinstance(payload, dict):
        return str(payload.get("description") or payload)[:500]
    return str(payload)[:500]


def _redact(message: str, token: str) -> str:
    return message.replace(token, "***") if token else message


def _message_id(response: httpx.Response) -> int | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("message_id"), int):
        return int(result["message_id"])
    return None
