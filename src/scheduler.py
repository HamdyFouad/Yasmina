"""Small dependency-free cron scheduler for daily scraper jobs.

Supports the standard five cron fields (minute, hour, day-of-month, month,
day-of-week) with ``*``, lists, ranges and step values. The scheduler is async
and uses the application timezone, so ``0 2 * * *`` means 02:00 local time.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "CronExpression",
    "CronScheduleError",
    "parse_cron",
    "run_scheduler",
]

logger = logging.getLogger("yasmina.scheduler")

_FIELD_LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_MAX_SEARCH_MINUTES = 366 * 24 * 60


class CronScheduleError(ValueError):
    """Raised when a cron expression or schedule cannot be used."""


def _parse_field(raw: str, low: int, high: int, *, day_of_week: bool = False) -> frozenset[int]:
    raw = raw.strip()
    if not raw:
        raise CronScheduleError("empty cron field")
    values: set[int] = set()

    def add_range(start: int, end: int, step: int) -> None:
        if step <= 0:
            raise CronScheduleError("cron step must be positive")
        if start > end:
            raise CronScheduleError(f"invalid cron range {start}-{end}")
        for value in range(start, end + 1, step):
            values.add(0 if day_of_week and value == 7 else value)

    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise CronScheduleError(f"invalid cron field {raw!r}")
        step = 1
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise CronScheduleError(f"invalid cron step {step_text!r}") from exc
            if step <= 0:
                raise CronScheduleError("cron step must be positive")
        if base == "*":
            add_range(low, high, step)
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise CronScheduleError(f"invalid cron range {base!r}") from exc
            add_range(start, end, step)
        else:
            try:
                value = int(base)
            except ValueError as exc:
                raise CronScheduleError(f"invalid cron value {base!r}") from exc
            if step != 1:
                add_range(value, high, step)
            else:
                values.add(0 if day_of_week and value == 7 else value)

    effective_low = 0 if day_of_week else low
    effective_high = 7 if day_of_week else high
    if any(value < effective_low or value > effective_high for value in values):
        raise CronScheduleError(f"cron value out of range {low}-{high}: {raw!r}")
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class CronExpression:
    expression: str
    timezone: ZoneInfo
    minute: frozenset[int]
    hour: frozenset[int]
    day_of_month: frozenset[int]
    month: frozenset[int]
    day_of_week: frozenset[int]
    dom_wildcard: bool
    dow_wildcard: bool

    @classmethod
    def parse(cls, expression: str, *, timezone: str = "Europe/Berlin") -> "CronExpression":
        fields = expression.split()
        if len(fields) != 5:
            raise CronScheduleError("cron expression must contain exactly five fields")
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise CronScheduleError(f"unknown timezone {timezone!r}") from exc

        return cls(
            expression=" ".join(fields),
            timezone=zone,
            minute=_parse_field(fields[0], *_FIELD_LIMITS[0]),
            hour=_parse_field(fields[1], *_FIELD_LIMITS[1]),
            day_of_month=_parse_field(fields[2], *_FIELD_LIMITS[2]),
            month=_parse_field(fields[3], *_FIELD_LIMITS[3]),
            day_of_week=_parse_field(fields[4], *_FIELD_LIMITS[4], day_of_week=True),
            dom_wildcard=fields[2].startswith("*"),
            dow_wildcard=fields[4].startswith("*"),
        )

    def matches(self, moment: datetime) -> bool:
        local = moment.replace(tzinfo=self.timezone) if moment.tzinfo is None else moment.astimezone(self.timezone)
        dom_match = local.day in self.day_of_month
        dow_match = ((local.weekday() + 1) % 7) in self.day_of_week
        if self.dom_wildcard or self.dow_wildcard:
            day_match = dom_match and dow_match
        else:
            day_match = dom_match or dow_match
        return (
            day_match
            and local.month in self.month
            and local.hour in self.hour
            and local.minute in self.minute
        )

    def next_after(self, moment: datetime) -> datetime:
        """Return the first matching minute strictly after ``moment``."""

        if moment.tzinfo is None:
            local = moment.replace(tzinfo=self.timezone)
        else:
            local = moment.astimezone(self.timezone)
        current = local.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(_MAX_SEARCH_MINUTES):
            if self.matches(current):
                return current
            current += timedelta(minutes=1)
        raise CronScheduleError(f"cron expression {self.expression!r} has no run within one year")


def parse_cron(expression: str, *, timezone: str = "Europe/Berlin") -> CronExpression:
    return CronExpression.parse(expression, timezone=timezone)


async def run_scheduler(
    job: Callable[[], Awaitable[object]],
    schedule: CronExpression,
    *,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    stop_event: asyncio.Event | None = None,
    max_runs: int | None = None,
) -> int:
    """Run ``job`` at every matching cron minute until stopped.

    ``max_runs`` is primarily useful for smoke tests and bounded maintenance
    runs. Job exceptions are logged and do not kill the scheduler.
    """

    if max_runs is not None and max_runs < 1:
        raise CronScheduleError("max_runs must be at least 1")
    clock = clock or (lambda: datetime.now(schedule.timezone))
    sleep = sleep or asyncio.sleep
    runs = 0
    while max_runs is None or runs < max_runs:
        if stop_event is not None and stop_event.is_set():
            break
        now = clock()
        now_local = now if now.tzinfo is not None else now.replace(tzinfo=schedule.timezone)
        next_run = schedule.next_after(now_local)
        delay = max(0.0, (next_run - now_local.astimezone(schedule.timezone)).total_seconds())
        if delay > 0:
            await sleep(delay)
        if stop_event is not None and stop_event.is_set():
            break
        try:
            await job()
        except Exception:
            logger.exception("scheduled scraper job failed")
        runs += 1
    return runs
