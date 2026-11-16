"""SMTP delivery for password-reset tokens.

The API never returns reset tokens in JSON. SMTP is preferred when configured
because it reaches the account's mailbox directly; Telegram remains the
best-effort fallback for operators without an e-mail address.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

import aiosmtplib

from src.config import Settings

__all__ = ["SmtpError", "SmtpNotifier"]

logger = logging.getLogger("yasmina.integrations.smtp")

SendCallable = Callable[..., Awaitable[Any]]


class SmtpError(RuntimeError):
    """Raised when an SMTP message cannot be sent."""


class SmtpNotifier:
    """Send transactional mail through a configured SMTP relay."""

    def __init__(self, settings: Settings, *, sender: SendCallable | None = None) -> None:
        self.settings = settings
        self._sender = sender or aiosmtplib.send

    @property
    def sender_email(self) -> str:
        return (self.settings.smtp_from_email or self.settings.smtp_username or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.settings.smtp_host and self.sender_email)

    async def send_password_reset(
        self,
        recipient: str,
        *,
        username: str,
        token: str,
        expires_minutes: int,
    ) -> bool:
        """Deliver a reset token, returning ``False`` when delivery fails."""

        if not self.configured:
            raise SmtpError("SMTP is not configured")
        if not recipient.strip():
            raise SmtpError("recipient e-mail address is missing")

        message = EmailMessage()
        message["From"] = formataddr((self.settings.smtp_from_name, self.sender_email))
        message["To"] = recipient.strip()
        message["Subject"] = "Yasmina password reset"
        message.set_content(
            f"Hello {username},\n\n"
            "A password reset was requested for your Yasmina account.\n\n"
            f"Reset token: {token}\n\n"
            f"The token is valid for {expires_minutes} minutes and can be used once.\n"
            "If you did not request this reset, ignore this e-mail.\n"
        )

        password = self.settings.smtp_password
        try:
            await self._sender(
                message,
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                username=self.settings.smtp_username,
                password=password.get_secret_value() if password is not None else None,
                start_tls=self.settings.smtp_starttls,
                use_tls=self.settings.smtp_ssl,
                timeout=self.settings.smtp_timeout_seconds,
            )
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            logger.warning("SMTP password-reset delivery failed: %s", exc)
            return False
        logger.info("SMTP password-reset message sent")
        return True
