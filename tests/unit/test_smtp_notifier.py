"""SMTP password-reset delivery tests with an injected sender."""

from __future__ import annotations

from uuid import uuid4

import aiosmtplib
import pytest

import src.api.routes.auth as auth_routes
from src.config import Settings
from src.integrations.smtp_notifier import SmtpError, SmtpNotifier
from src.repositories.user_repository import UserView


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_from_email": "noreply@example.test",
        "smtp_from_name": "Yasmina",
        "smtp_username": "mailer",
        "smtp_password": "secret",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class FakeSender:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[object, dict[str, object]]] = []

    async def __call__(self, message: object, **kwargs: object) -> None:
        self.calls.append((message, kwargs))
        if self.error is not None:
            raise self.error


async def test_smtp_delivers_reset_token() -> None:
    sender = FakeSender()
    notifier = SmtpNotifier(make_settings(), sender=sender)

    assert await notifier.send_password_reset(
        "user@example.test",
        username="operator",
        token="reset-token-123",
        expires_minutes=15,
    )

    message, kwargs = sender.calls[0]
    assert message["To"] == "user@example.test"  # type: ignore[index]
    assert message["Subject"] == "Yasmina password reset"  # type: ignore[index]
    assert "reset-token-123" in message.get_content()  # type: ignore[attr-defined]
    assert kwargs["hostname"] == "smtp.example.test"
    assert kwargs["port"] == 587
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False
    assert kwargs["password"] == "secret"


async def test_smtp_failure_is_reported_without_raising() -> None:
    notifier = SmtpNotifier(
        make_settings(),
        sender=FakeSender(error=aiosmtplib.SMTPException("relay unavailable")),
    )

    assert (
        await notifier.send_password_reset(
            "user@example.test",
            username="operator",
            token="reset-token-123",
            expires_minutes=15,
        )
        is False
    )


async def test_unconfigured_smtp_raises_configuration_error() -> None:
    notifier = SmtpNotifier(make_settings(smtp_host=None))

    assert notifier.configured is False
    with pytest.raises(SmtpError, match="not configured"):
        await notifier.send_password_reset(
            "user@example.test",
            username="operator",
            token="reset-token-123",
            expires_minutes=15,
        )


def test_smtp_transport_modes_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        make_settings(smtp_starttls=True, smtp_ssl=True)


async def test_password_reset_delivery_prefers_smtp(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    delivered: list[dict[str, object]] = []

    class FakeSmtp:
        configured = True

        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def send_password_reset(self, recipient: str, **kwargs: object) -> bool:
            delivered.append({"recipient": recipient, **kwargs})
            return True

    class FakeTelegram:
        configured = False

        def __init__(self, settings: Settings) -> None:
            self.settings = settings

    monkeypatch.setattr(auth_routes, "SmtpNotifier", FakeSmtp)
    monkeypatch.setattr(auth_routes, "TelegramNotifier", FakeTelegram)
    user = UserView(
        id=uuid4(),
        username="operator",
        email="operator@example.test",
        roles=(),
        is_active=True,
    )

    assert await auth_routes._deliver_reset_token(make_settings(), user, "reset-token") is True
    assert delivered == [
        {
            "recipient": "operator@example.test",
            "username": "operator",
            "token": "reset-token",
            "expires_minutes": 15,
        }
    ]
