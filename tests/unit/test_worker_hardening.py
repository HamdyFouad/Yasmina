"""Worker hardening tests: advisory locks, shutdown signals and alerts."""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import src.worker as worker_module
from src.config import Settings
from src.models.enums import ProcurementProcedure
from src.models.schemas import Tender
from src.repositories.ingestion_repository import IngestionJobView
from src.repositories.tender_repository import StoredTender
from src.scrapers.base_scraper import ScrapeReport
from src.scrapers.models import DiscoveredTender
from src.worker import (
    _notify_dead_letter,
    _process_loop,
    install_shutdown_handlers,
    run_scheduled,
    run_scrape_once,
)

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


class FakeStorage:
    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        return object_name


class FakeRepository:
    def __init__(self) -> None:
        self.saved: list[Tender] = []

    async def get_by_number(self, tender_number: str) -> StoredTender | None:
        return None

    async def save(self, tender: Tender, **kwargs: object) -> StoredTender:
        self.saved.append(tender)
        return StoredTender(tender=tender)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def append(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class FakeScraper:
    def __init__(self, *, stop_event: asyncio.Event | None = None) -> None:
        self.stop_event = stop_event
        self.calls = 0

    async def scrape(self, *, max_pages: int | None = None, since: object | None = None) -> ScrapeReport:
        self.calls += 1
        if self.stop_event is not None:
            self.stop_event.set()
        return ScrapeReport(
            portal="vergabe.bayern",
            discovered=(make_discovered(),),
            pages_fetched=1,
            skipped_cpv=0,
            skipped_invalid=0,
            errors=(),
        )


class FakeLocker:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = list(outcomes)
        self.acquired: list[str] = []
        self.released: list[str] = []

    async def acquire(self, portal: str) -> bool:
        self.acquired.append(portal)
        return self.outcomes.pop(0)

    async def release(self, portal: str) -> None:
        self.released.append(portal)

    async def release_all(self) -> None:
        self.released.extend(self.acquired)


class FakeRuns:
    def __init__(self) -> None:
        self.started: list[dict[str, object]] = []
        self.finished: list[dict[str, object]] = []

    async def start(self, **kwargs: object) -> SimpleNamespace:
        self.started.append(kwargs)
        return SimpleNamespace(id=uuid4())

    async def finish(self, run_id: UUID, **kwargs: object) -> SimpleNamespace:
        self.finished.append({"run_id": run_id, **kwargs})
        return SimpleNamespace(id=run_id)


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def notify(self, text: str) -> bool:
        self.messages.append(text)
        return True


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        scraper_request_delay_seconds=0,
        scraper_harvest_attachments=False,
    )


def make_discovered(portal_id: str = "2026-001") -> DiscoveredTender:
    return DiscoveredTender(
        portal="vergabe.bayern",
        portal_code="BY",
        portal_id=portal_id,
        title="Reinigung von Verwaltungsgebäuden",
        client_name="Stadt München",
        detail_url=f"https://www.vergabe.bayern.de/ausschreibung/{portal_id}",
        submission_deadline=datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
        cpv_codes=("33760000-5",),
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
    )


async def test_scrape_skips_portal_when_advisory_lock_is_held() -> None:
    runs = FakeRuns()
    locker = FakeLocker([False])
    factory_calls: list[str] = []

    def factory(portal: str, settings: Settings, storage: object) -> FakeScraper:
        factory_calls.append(portal)
        return FakeScraper()

    summaries = await run_scrape_once(
        "bayern",
        settings=make_settings(),
        repository=FakeRepository(),  # type: ignore[arg-type]
        storage=FakeStorage(),
        runs=runs,  # type: ignore[arg-type]
        locks=locker,
        scraper_factory=factory,  # type: ignore[arg-type]
    )

    assert factory_calls == []
    assert locker.acquired == ["bayern"]
    assert locker.released == []
    assert summaries[0]["locked"] is True
    assert runs.finished[0]["status"] == "skipped"


async def test_scrape_releases_lock_after_inflight_portal_on_shutdown() -> None:
    stop_event = asyncio.Event()
    runs = FakeRuns()
    locker = FakeLocker([True, True])
    scraper = FakeScraper(stop_event=stop_event)
    calls: list[str] = []

    def factory(portal: str, settings: Settings, storage: object) -> FakeScraper:
        calls.append(portal)
        return scraper

    summaries = await run_scrape_once(
        "all",
        settings=make_settings(),
        repository=FakeRepository(),  # type: ignore[arg-type]
        storage=FakeStorage(),
        runs=runs,  # type: ignore[arg-type]
        locks=locker,
        scraper_factory=factory,  # type: ignore[arg-type]
        stop_event=stop_event,
    )

    assert calls == ["bayern"]
    assert locker.acquired == ["bayern"]
    assert locker.released == ["bayern"]
    assert [summary["portal"] for summary in summaries] == ["bayern"]
    assert runs.finished[0]["status"] == "succeeded"
    assert runs.finished[0]["items_processed"] == 1


async def test_process_loop_finishes_inflight_job_then_stops() -> None:
    stop_event = asyncio.Event()
    notifier = FakeNotifier()
    calls: list[dict[str, object]] = []

    async def process_once(**kwargs: object) -> list[dict[str, str]]:
        calls.append(kwargs)
        event = kwargs["stop_event"]
        assert isinstance(event, asyncio.Event)
        event.set()
        return [{"job_id": "job-1", "tender_id": "tender-1", "status": "completed"}]

    await _process_loop(
        make_settings(),
        5,
        60.0,
        stop_event=stop_event,
        notifier=notifier,  # type: ignore[arg-type]
        process_once=process_once,
    )

    assert len(calls) == 1
    assert calls[0]["notifier"] is notifier


async def test_install_shutdown_handlers_registers_both_signals_and_restores() -> None:
    stop_event = asyncio.Event()

    class Loop:
        def __init__(self) -> None:
            self.handlers: dict[signal.Signals, tuple[object, tuple[object, ...]]] = {}
            self.removed: list[signal.Signals] = []

        def add_signal_handler(self, signum: signal.Signals, callback: object, *args: object) -> None:
            self.handlers[signum] = (callback, args)

        def remove_signal_handler(self, signum: signal.Signals) -> None:
            self.removed.append(signum)

    loop = Loop()
    restore = install_shutdown_handlers(stop_event, loop=loop)  # type: ignore[arg-type]

    assert set(loop.handlers) == {signal.SIGINT, signal.SIGTERM}
    callback, args = loop.handlers[signal.SIGTERM]
    callback(*args)  # type: ignore[operator]
    assert stop_event.is_set()

    restore()
    assert set(loop.removed) == {signal.SIGINT, signal.SIGTERM}


async def test_install_shutdown_handlers_falls_back_to_signal_module(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    stop_event = asyncio.Event()
    registered: dict[signal.Signals, object] = {}
    previous = object()

    class Loop:
        def add_signal_handler(self, *args: object) -> None:
            raise NotImplementedError

        def call_soon_threadsafe(self, callback: object, *args: object) -> None:  # type: ignore[no-untyped-def]
            callback(*args)  # type: ignore[operator]

    monkeypatch.setattr(signal, "getsignal", lambda signum: previous)
    monkeypatch.setattr(signal, "signal", lambda signum, handler: registered.__setitem__(signum, handler))

    restore = install_shutdown_handlers(stop_event, loop=Loop())  # type: ignore[arg-type]
    assert set(registered) == {signal.SIGINT, signal.SIGTERM}

    handler = registered[signal.SIGTERM]
    handler(signal.SIGTERM, None)  # type: ignore[operator]
    assert stop_event.is_set()

    restore()
    assert registered[signal.SIGTERM] is previous


async def test_dead_letter_alert_contains_job_and_error() -> None:
    notifier = FakeNotifier()
    job = IngestionJobView(
        id=uuid4(),
        tender_id=uuid4(),
        portal_name="vergabe.bayern",
        status="processing",
        attempts=5,
        max_attempts=5,
        attachments=(),
        raw_object=None,
    )

    sent = await _notify_dead_letter(
        notifier,  # type: ignore[arg-type]
        job=job,
        attempts=job.attempts,
        error="DeepSeek timeout",
    )

    assert sent is True
    assert "dead-letter" in notifier.messages[0]
    assert str(job.id) in notifier.messages[0]
    assert "DeepSeek timeout" in notifier.messages[0]


async def test_scheduled_scrape_failure_sends_alert(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    notifier = FakeNotifier()

    async def fake_scheduler(job, schedule, **kwargs):  # type: ignore[no-untyped-def]
        await job()
        return 1

    async def failing_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("portal returned 503")

    monkeypatch.setattr(worker_module, "run_scheduler", fake_scheduler)
    monkeypatch.setattr(worker_module, "_run_once_with_db", failing_run)

    with pytest.raises(RuntimeError, match="503"):
        await run_scheduled(
            settings=make_settings(),
            portal="bayern",
            cron="0 2 * * *",
            notifier=notifier,  # type: ignore[arg-type]
        )

    assert "scheduled scrape failed" in notifier.messages[0]
    assert "portal returned 503" in notifier.messages[0]
