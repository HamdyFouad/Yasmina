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

    async def _send(self, message: EmailMessage, *, context: str) -> bool:
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
            logger.warning("SMTP %s delivery failed: %s", context, exc)
            return False
        logger.info("SMTP %s message sent", context)
        return True

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
        return await self._send(message, context="password-reset")

    async def send_invoice(
        self,
        recipient: str,
        *,
        invoice_number: str,
        pdf: bytes,
        xml: bytes,
        buyer_name: str | None = None,
    ) -> bool:
        """Send the ZUGFeRD PDF and XRechnung XML to the buyer."""

        if not self.configured:
            raise SmtpError("SMTP is not configured")
        if not recipient.strip():
            raise SmtpError("recipient e-mail address is missing")

        message = EmailMessage()
        message["From"] = formataddr((self.settings.smtp_from_name, self.sender_email))
        message["To"] = recipient.strip()
        message["Subject"] = f"Yasmina invoice {invoice_number}"
        greeting = f"Hello {buyer_name}," if buyer_name else "Hello,"
        message.set_content(
            f"{greeting}\n\n"
            f"Please find invoice {invoice_number} attached as a ZUGFeRD PDF and "
            "XRechnung XML document.\n\n"
            "This message was sent by Yasmina.\n"
        )
        message.add_attachment(
            pdf,
            maintype="application",
            subtype="pdf",
            filename=f"{invoice_number}.pdf",
        )
        message.add_attachment(
            xml,
            maintype="application",
            subtype="xml",
            filename=f"{invoice_number}.xml",
        )
        return await self._send(message, context="invoice")

    async def send_dunning(
        self,
        recipient: str,
        *,
        invoice_number: str,
        buyer_name: str | None,
        amount_due: str,
        due_date: str,
        level: int,
        currency: str = "EUR",
    ) -> bool:
        """Send one dunning reminder for an overdue invoice."""

        if not self.configured:
            raise SmtpError("SMTP is not configured")
        if not recipient.strip():
            raise SmtpError("recipient e-mail address is missing")

        reminder = "Reminder 1" if level == 1 else "Reminder 2"
        message = EmailMessage()
        message["From"] = formataddr((self.settings.smtp_from_name, self.sender_email))
        message["To"] = recipient.strip()
        message["Subject"] = f"Yasmina {reminder.lower()} for invoice {invoice_number}"
        greeting = f"Hello {buyer_name}," if buyer_name else "Hello,"
        urgency = (
            "Please arrange payment at your earliest convenience."
            if level == 1
            else "This invoice is now significantly overdue. Please arrange payment immediately "
            "or contact us to discuss the outstanding balance."
        )
        message.set_content(
            f"{greeting}\n\n"
            f"Our records show that invoice {invoice_number} remains unpaid.\n\n"
            f"Outstanding amount: {amount_due} {currency}\n"
            f"Due date: {due_date}\n\n"
            f"{urgency}\n\n"
            "If you have already paid, please disregard this message.\n\n"
            "This message was sent by Yasmina.\n"
        )
        return await self._send(message, context="dunning")
