"""Automated dunning escalation for overdue customer invoices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from src.config import Settings
from src.db import get_session_factory
from src.integrations.smtp_notifier import SmtpNotifier
from src.repositories.invoice_repository import DunningCandidate, InvoiceRepository

__all__ = [
    "DunningError",
    "DunningNotifier",
    "DunningRunResult",
    "DunningSent",
    "next_dunning_level",
    "run_dunning",
    "run_dunning_once",
]


class DunningError(RuntimeError):
    """Raised when dunning cannot run with the current configuration."""


class DunningNotifier(Protocol):
    @property
    def configured(self) -> bool: ...

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
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class DunningSent:
    invoice_id: UUID
    invoice_number: str
    level: int
    recipient: str
    amount_due: Decimal
    days_overdue: int


@dataclass(frozen=True, slots=True)
class DunningRunResult:
    sent: tuple[DunningSent, ...]
    candidates: int
    skipped: int
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": [
                {
                    "invoice_id": str(item.invoice_id),
                    "invoice_number": item.invoice_number,
                    "level": item.level,
                    "recipient": item.recipient,
                    "amount_due": str(item.amount_due),
                    "days_overdue": item.days_overdue,
                }
                for item in self.sent
            ],
            "sent_count": len(self.sent),
            "candidates": self.candidates,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
        }


def next_dunning_level(
    *,
    days_overdue: int,
    current_level: int,
    first_reminder_days: int,
    second_reminder_days: int,
) -> int | None:
    """Return the next reminder level, or ``None`` when no reminder is due."""

    if current_level >= 2 or days_overdue < first_reminder_days:
        return None
    if days_overdue >= second_reminder_days:
        return 2 if current_level < 2 else None
    if days_overdue >= first_reminder_days:
        return 1 if current_level < 1 else None
    return None


async def run_dunning(
    repository: InvoiceRepository,
    notifier: DunningNotifier,
    *,
    first_reminder_days: int,
    second_reminder_days: int,
    today: date | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> DunningRunResult:
    """Evaluate candidates and send at most one reminder per invoice per run."""

    if not notifier.configured and not dry_run:
        raise DunningError("SMTP is not configured for dunning")
    current = today or datetime.now(UTC).date()
    sent_at = now or datetime.now(UTC)
    candidates = await repository.list_dunning_candidates(
        today=current,
        first_reminder_days=first_reminder_days,
        second_reminder_days=second_reminder_days,
    )
    sent: list[DunningSent] = []
    skipped = 0
    for candidate in candidates:
        level = next_dunning_level(
            days_overdue=candidate.days_overdue,
            current_level=candidate.dunning_level,
            first_reminder_days=first_reminder_days,
            second_reminder_days=second_reminder_days,
        )
        if level is None or level <= candidate.dunning_level:
            skipped += 1
            continue
        if not dry_run:
            delivered = await notifier.send_dunning(
                candidate.buyer_email,
                invoice_number=candidate.invoice_number,
                buyer_name=candidate.buyer_name,
                amount_due=str(candidate.amount_due),
                due_date=candidate.due_date.isoformat(),
                level=level,
                currency=candidate.currency,
            )
            if not delivered:
                skipped += 1
                continue
            recorded = await repository.record_dunning(
                candidate.id,
                level=level,
                recipient=candidate.buyer_email,
                now=sent_at,
            )
            if not recorded:
                skipped += 1
                continue
        sent.append(_sent(candidate, level))
    return DunningRunResult(
        sent=tuple(sent),
        candidates=len(candidates),
        skipped=skipped,
        dry_run=dry_run,
    )


async def run_dunning_once(
    settings: Settings,
    *,
    notifier: DunningNotifier | None = None,
    today: date | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> DunningRunResult:
    """Run overdue maintenance followed by the dunning ladder for one day."""

    active_notifier = notifier or SmtpNotifier(settings)
    factory = get_session_factory(settings)
    async with factory() as session:
        repository = InvoiceRepository(session)
        await repository.mark_overdue_batch(today=today)
        return await run_dunning(
            repository,
            active_notifier,
            first_reminder_days=settings.dunning_reminder_1_days,
            second_reminder_days=settings.dunning_reminder_2_days,
            today=today,
            now=now,
            dry_run=dry_run,
        )


def _sent(candidate: DunningCandidate, level: int) -> DunningSent:
    return DunningSent(
        invoice_id=candidate.id,
        invoice_number=candidate.invoice_number,
        level=level,
        recipient=candidate.buyer_email,
        amount_due=candidate.amount_due,
        days_overdue=candidate.days_overdue,
    )
