"""Daily overdue-invoice maintenance."""

from __future__ import annotations

from datetime import date

from src.config import Settings
from src.db import get_session_factory
from src.repositories.invoice_repository import InvoiceRepository

__all__ = ["mark_overdue_invoices", "mark_overdue_once"]


async def mark_overdue_invoices(
    repository: InvoiceRepository,
    *,
    today: date | None = None,
) -> int:
    """Mark all eligible invoices overdue and append their GoBD events."""

    return len(await repository.mark_overdue_batch(today=today))


async def mark_overdue_once(settings: Settings, *, today: date | None = None) -> int:
    factory = get_session_factory(settings)
    async with factory() as session:
        return await mark_overdue_invoices(
            InvoiceRepository(session),
            today=today,
        )
