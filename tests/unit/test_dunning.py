"""Dunning escalation and schedule tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.repositories.invoice_repository import DunningCandidate
from src.services.dunning import (
    DunningError,
    next_dunning_level,
    run_dunning,
)


def make_candidate(*, days_overdue: int, level: int = 0) -> DunningCandidate:
    return DunningCandidate(
        id=uuid4(),
        invoice_number="INV-2026-0001",
        buyer_name="Stadt Muster",
        buyer_email="buyer@example.de",
        due_date=date(2026, 1, 1),
        total_gross=Decimal("119.00"),
        paid_amount=Decimal("0.00"),
        credited_amount=Decimal("0.00"),
        currency="EUR",
        dunning_level=level,
        days_overdue=days_overdue,
    )


def test_dunning_ladder_escalates_once_per_level() -> None:
    assert (
        next_dunning_level(
            days_overdue=2,
            current_level=0,
            first_reminder_days=3,
            second_reminder_days=14,
        )
        is None
    )
    assert (
        next_dunning_level(
            days_overdue=3,
            current_level=0,
            first_reminder_days=3,
            second_reminder_days=14,
        )
        == 1
    )
    assert (
        next_dunning_level(
            days_overdue=14,
            current_level=1,
            first_reminder_days=3,
            second_reminder_days=14,
        )
        == 2
    )
    assert (
        next_dunning_level(
            days_overdue=30,
            current_level=2,
            first_reminder_days=3,
            second_reminder_days=14,
        )
        is None
    )


class FakeRepository:
    def __init__(self, candidates: list[DunningCandidate]) -> None:
        self.candidates = candidates
        self.recorded: list[tuple[object, int, str]] = []

    async def list_dunning_candidates(self, **kwargs: object) -> list[DunningCandidate]:
        return self.candidates

    async def record_dunning(
        self, invoice_id: object, *, level: int, recipient: str, now: datetime | None = None
    ) -> bool:
        self.recorded.append((invoice_id, level, recipient))
        return True


class FakeNotifier:
    def __init__(self, *, configured: bool = True, delivered: bool = True) -> None:
        self._configured = configured
        self.delivered = delivered
        self.sent: list[tuple[str, int, str]] = []

    @property
    def configured(self) -> bool:
        return self._configured

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
        self.sent.append((invoice_number, level, recipient))
        return self.delivered


async def test_runner_sends_reminder_and_records_the_level() -> None:
    repository = FakeRepository([make_candidate(days_overdue=5)])
    notifier = FakeNotifier()

    result = await run_dunning(
        repository,  # type: ignore[arg-type]
        notifier,
        first_reminder_days=3,
        second_reminder_days=14,
        today=date(2026, 2, 1),
        now=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
    )

    assert len(result.sent) == 1
    assert result.sent[0].level == 1
    assert notifier.sent == [("INV-2026-0001", 1, "buyer@example.de")]
    assert repository.recorded == [(result.sent[0].invoice_id, 1, "buyer@example.de")]
    assert result.as_dict()["sent_count"] == 1


async def test_runner_skips_delivery_failure_and_unconfigured_smtp() -> None:
    repository = FakeRepository([make_candidate(days_overdue=5)])
    notifier = FakeNotifier(delivered=False)

    result = await run_dunning(
        repository,  # type: ignore[arg-type]
        notifier,
        first_reminder_days=3,
        second_reminder_days=14,
    )
    assert result.sent == ()
    assert result.skipped == 1
    assert repository.recorded == []

    with pytest.raises(DunningError):
        await run_dunning(
            repository,  # type: ignore[arg-type]
            FakeNotifier(configured=False),
            first_reminder_days=3,
            second_reminder_days=14,
        )
