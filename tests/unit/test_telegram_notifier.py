"""Telegram alert transport tests with an injected HTTP client."""

from __future__ import annotations

import httpx
import pytest

from src.config import Settings
from src.integrations.telegram_notifier import (
    TelegramConfigurationError,
    TelegramError,
    TelegramNotifier,
)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "telegram_bot_token": "123456:secret-token",
        "telegram_approval_chat_id": "-1001234567890",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class FakeClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, *, json: dict[str, object]) -> httpx.Response:
        self.calls.append((url, json))
        return self.responses.pop(0)


async def test_send_alert_posts_to_configured_chat() -> None:
    client = FakeClient([httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})])
    notifier = TelegramNotifier(make_settings(), client=client)  # type: ignore[arg-type]

    result = await notifier.send_alert("scheduled scrape failed")

    assert result.chat_id == "-1001234567890"
    assert result.message_id == 42
    url, payload = client.calls[0]
    assert url.endswith("/sendMessage")
    assert payload["chat_id"] == "-1001234567890"
    assert payload["text"] == "scheduled scrape failed"


async def test_retryable_telegram_failure_is_retried() -> None:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FakeClient(
        [
            httpx.Response(503, json={"ok": False, "description": "temporarily unavailable"}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 43}}),
        ]
    )
    notifier = TelegramNotifier(
        make_settings(telegram_retry_backoff_seconds=0.25),
        client=client,  # type: ignore[arg-type]
        sleep=sleep,
    )

    result = await notifier.send_alert("dead-letter job")

    assert result.message_id == 43
    assert len(client.calls) == 2
    assert sleeps == [0.25]


async def test_unconfigured_telegram_notifier_fails_softly() -> None:
    notifier = TelegramNotifier(make_settings(telegram_approval_chat_id=None))

    with pytest.raises(TelegramConfigurationError):
        await notifier.send_alert("test")
    assert await notifier.notify("test") is False


async def test_permanent_telegram_error_is_raised() -> None:
    client = FakeClient([httpx.Response(400, json={"ok": False, "description": "chat not found"})])
    notifier = TelegramNotifier(make_settings(), client=client)  # type: ignore[arg-type]

    with pytest.raises(TelegramError, match="chat not found"):
        await notifier.send_alert("test")
