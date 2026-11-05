"""Cron parser and async scheduler tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.scheduler import CronExpression, CronScheduleError, parse_cron, run_scheduler

BERLIN = ZoneInfo("Europe/Berlin")


def test_daily_cron_next_run_uses_application_timezone() -> None:
    schedule = CronExpression.parse("0 2 * * *", timezone="Europe/Berlin")
    first = datetime(2026, 1, 1, 1, 30, tzinfo=BERLIN)
    second = datetime(2026, 1, 1, 2, 0, tzinfo=BERLIN)

    assert schedule.next_after(first) == datetime(2026, 1, 1, 2, 0, tzinfo=BERLIN)
    assert schedule.next_after(second) == datetime(2026, 1, 2, 2, 0, tzinfo=BERLIN)


def test_cron_lists_ranges_and_steps() -> None:
    schedule = parse_cron("*/15 8-10 * * 1-5", timezone="Europe/Berlin")

    assert schedule.minute == frozenset({0, 15, 30, 45})
    assert schedule.hour == frozenset({8, 9, 10})
    assert schedule.day_of_week == frozenset({1, 2, 3, 4, 5})
    assert schedule.matches(datetime(2026, 1, 5, 8, 15, tzinfo=BERLIN))  # Monday
    assert not schedule.matches(datetime(2026, 1, 4, 8, 15, tzinfo=BERLIN))  # Sunday


def test_invalid_cron_expressions_are_rejected() -> None:
    with pytest.raises(CronScheduleError, match="five fields"):
        parse_cron("0 2 * *")
    with pytest.raises(CronScheduleError, match="out of range"):
        parse_cron("60 * * * *")
    with pytest.raises(CronScheduleError, match="invalid cron range"):
        parse_cron("0 5-2 * * *")
    with pytest.raises(CronScheduleError, match="timezone"):
        parse_cron("0 2 * * *", timezone="Not/AZone")


async def test_run_scheduler_executes_job_at_next_minute() -> None:
    schedule = CronExpression.parse("0 2 * * *", timezone="Europe/Berlin")
    current = datetime(2026, 1, 1, 1, 59, tzinfo=BERLIN)
    sleeps: list[float] = []
    executions: list[datetime] = []

    def clock() -> datetime:
        return current

    async def sleep(delay: float) -> None:
        nonlocal current
        sleeps.append(delay)
        current += timedelta(seconds=delay)

    async def job() -> None:
        executions.append(clock())

    runs = await run_scheduler(
        job,
        schedule,
        clock=clock,
        sleep=sleep,
        max_runs=1,
    )

    assert runs == 1
    assert sleeps == [60.0]
    assert executions == [datetime(2026, 1, 1, 2, 0, tzinfo=BERLIN)]


async def test_run_scheduler_logs_job_failures_and_continues() -> None:
    schedule = CronExpression.parse("* * * * *", timezone="Europe/Berlin")
    current = datetime(2026, 1, 1, 12, 0, tzinfo=BERLIN)
    calls: list[int] = []

    def clock() -> datetime:
        return current

    async def sleep(delay: float) -> None:
        nonlocal current
        current += timedelta(seconds=delay)

    async def job() -> None:
        calls.append(1)
        raise RuntimeError("temporary portal failure")

    runs = await run_scheduler(job, schedule, clock=clock, sleep=sleep, max_runs=2)

    assert runs == 2
    assert len(calls) == 2
