"""CLI worker for one-off portal scrapes and daily cron scheduling."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, TypeVar

from src.api.context import build_agent_context
from src.config import Settings, get_settings
from src.db import dispose_engine, get_engine, get_session_factory
from src.integrations.minio_client import MinioObjectStorage
from src.integrations.telegram_notifier import TelegramNotifier
from src.logging_config import configure_logging
from src.repositories.ingestion_repository import (
    IngestionJobView,
    ScraperStateRepository,
    TenderIngestionJobRepository,
)
from src.repositories.tender_repository import TenderRepository
from src.repositories.worker_run_repository import WorkerRunRepository
from src.scheduler import CronExpression, CronScheduleError, run_scheduler, wait_for_stop
from src.scrapers.api_feeds import build_api_scraper
from src.scrapers.base_scraper import BaseScraper, ObjectWriter, ScrapeReport
from src.scrapers.bayern_vergabe import BayernVergabeScraper
from src.scrapers.domain_limits import (
    DomainRateLimiter,
    build_domain_rate_limiter,
)
from src.scrapers.engines import build_engine_scraper
from src.scrapers.ingestion import IngestionPipeline, PortalIngestionBridge, PortalIngestionResult
from src.scrapers.oeffentlichevergabe import OeffentlicheVergabeScraper
from src.scrapers.portal_config import PortalTarget, load_portal_targets
from src.services.audit_service import AuditService
from src.services.ingestion_processor import TenderIngestionProcessor
from src.services.worker_lock import (
    DatabasePortalLock,
    DomainLocker,
    PortalLocker,
    build_domain_locker,
)

__all__ = [
    "PORTALS",
    "build_scraper",
    "install_shutdown_handlers",
    "main",
    "process_jobs_once",
    "request_shutdown",
    "run_scheduled",
    "run_scrape_once",
]

logger = logging.getLogger("yasmina.worker")

T = TypeVar("T")

PORTALS: dict[str, type[BaseScraper]] = {
    "bayern": BayernVergabeScraper,
    "bund": OeffentlicheVergabeScraper,
}
ScraperFactory = Callable[[str, Settings, ObjectWriter], BaseScraper]


@dataclass(slots=True)
class _PreparedPortal:
    target: PortalTarget
    max_pages: int | None
    since: datetime | None
    scraper: BaseScraper | None = None
    run_id: Any = None
    report: ScrapeReport | None = None
    used_api: bool = False
    error: Exception | None = None


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def request_shutdown(stop_event: asyncio.Event) -> None:
    """Set the cooperative shutdown flag (called by signal handlers)."""

    if not stop_event.is_set():
        logger.info("shutdown requested; finishing in-flight work before exiting")
    stop_event.set()


def install_shutdown_handlers(
    stop_event: asyncio.Event,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Callable[[], None]:
    """Install SIGINT/SIGTERM handlers and return a restore callback.

    ``loop.add_signal_handler`` is used where supported. Windows' Proactor loop
    does not implement it, so the fallback registers a plain ``signal`` handler
    that schedules ``stop_event.set`` on the running loop.
    """

    active_loop = loop or asyncio.get_running_loop()
    restorers: list[tuple[signal.Signals, Any]] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            active_loop.add_signal_handler(signum, request_shutdown, stop_event)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                previous = signal.getsignal(signum)

                def handler(
                    _signum: int,
                    _frame: Any,
                    *,
                    _loop: asyncio.AbstractEventLoop = active_loop,
                ) -> None:
                    _loop.call_soon_threadsafe(request_shutdown, stop_event)

                signal.signal(signum, handler)
            except (ValueError, RuntimeError) as exc:
                logger.warning("could not install %s shutdown handler: %s", signum.name, exc)
                continue
            restorers.append((signum, previous))
        else:
            restorers.append((signum, "loop"))

    def restore() -> None:
        for signum, previous in restorers:
            if previous == "loop":
                active_loop.remove_signal_handler(signum)
            else:
                try:
                    signal.signal(signum, previous)
                except (ValueError, RuntimeError):  # pragma: no cover - non-main thread
                    logger.debug("could not restore %s handler", signum.name)

    return restore


async def _run_with_shutdown(
    operation: Callable[[asyncio.Event], Awaitable[T]],
) -> T:
    stop_event = asyncio.Event()
    restore = install_shutdown_handlers(stop_event)
    try:
        return await operation(stop_event)
    finally:
        restore()


def _locked_summary(portal: str) -> dict[str, Any]:
    return {
        "portal": portal,
        "pages_fetched": 0,
        "discovered": 0,
        "skipped_cpv": 0,
        "skipped_invalid": 0,
        "skipped_stale": 0,
        "errors": [],
        "results": [],
        "since": None,
        "locked": True,
    }


def _dead_letter_message(
    *,
    job_id: Any,
    tender_id: Any,
    portal_name: str,
    attempts: int,
    error: str,
) -> str:
    return (
        "Yasmina ingestion job moved to dead-letter after "
        f"{attempts} attempts.\n"
        f"job_id: {job_id}\n"
        f"tender_id: {tender_id}\n"
        f"portal: {portal_name}\n"
        f"error: {error[:1500]}"
    )


def _scrape_failure_message(*, portal: str, cron: str, worker_id: str, error: str) -> str:
    return (
        "Yasmina scheduled scrape failed.\n"
        f"portal: {portal}\n"
        f"schedule: {cron}\n"
        f"worker: {worker_id}\n"
        f"error: {error[:1500]}"
    )


async def _notify_dead_letter(
    notifier: TelegramNotifier | None,
    *,
    job: IngestionJobView,
    attempts: int,
    error: str,
) -> bool:
    if notifier is None:
        return False
    return await notifier.notify(
        _dead_letter_message(
            job_id=job.id,
            tender_id=job.tender_id,
            portal_name=job.portal_name,
            attempts=attempts,
            error=error,
        )
    )


def _load_targets(
    settings: Settings,
    *,
    enabled_only: bool = True,
) -> list[PortalTarget]:
    return load_portal_targets(settings.portal_config_file, enabled_only=enabled_only)


def _targets_for(
    portal: str,
    targets: Sequence[PortalTarget] | None,
    *,
    settings: Settings,
) -> list[PortalTarget]:
    available = list(targets) if targets is not None else _load_targets(settings)
    if portal == "all":
        return available
    selected = next((target for target in available if target.id == portal), None)
    if selected is None:
        raise ValueError(
            f"unknown portal {portal!r}; expected one of all, {sorted(target.id for target in available)}"
        )
    return [selected]


def build_engine_portal_scraper(
    target: PortalTarget,
    settings: Settings,
    storage: ObjectWriter | None = None,
    *,
    rate_limiter: DomainRateLimiter | None = None,
) -> BaseScraper:
    return build_engine_scraper(target, settings, storage, rate_limiter=rate_limiter)


def build_scraper(portal: str, settings: Settings, storage: ObjectWriter) -> BaseScraper:
    """Legacy id-based factory kept for callers and tests.

    The worker itself prefers ``build_engine_portal_scraper``/API adapters via a
    ``PortalTarget``; this helper resolves the id from the YAML catalogue.
    """

    target = _targets_for(portal, None, settings=settings)[0]
    return build_engine_portal_scraper(target, settings, storage)


def _result_dict(result: PortalIngestionResult) -> dict[str, Any]:
    return {
        "tender_id": str(result.tender_id),
        "tender_number": result.tender_number,
        "created": result.created,
        "deduplicated": result.deduplicated,
        "duplicate_of": str(result.duplicate_of) if result.duplicate_of is not None else None,
        "duplicate_reason": result.duplicate_reason,
        "pipeline_triggered": result.pipeline_triggered,
        "raw_object": result.raw_object,
        "job_id": str(result.job_id) if result.job_id is not None else None,
    }


async def _finish_run(
    runs: WorkerRunRepository,
    run_id: Any,
    **kwargs: Any,
) -> None:
    try:
        await runs.finish(run_id, **kwargs)
    except Exception:  # pragma: no cover - metrics must not mask the scrape outcome
        logger.exception("could not record scrape run id=%s", run_id)


async def _record_locked(
    target: PortalTarget,
    worker_id: str,
    runs: WorkerRunRepository | None,
    summaries: list[dict[str, Any]],
    *,
    domain: str | None = None,
) -> None:
    if runs is not None:
        try:
            run = await runs.start(
                run_type="scrape",
                resource=target.id,
                worker_id=worker_id,
                details={"locked": True, "domain": domain},
            )
            await _finish_run(
                runs,
                run.id,
                status="skipped",
                details={"locked": True, "portal": target.id, "domain": domain},
            )
        except Exception:  # pragma: no cover - metrics must not stop the worker
            logger.exception("could not record locked scrape run portal=%s", target.id)
    summary = _locked_summary(target.id)
    if domain is not None:
        summary["domain"] = domain
        summary["domain_locked"] = True
    summaries.append(summary)


def _error_summary(
    target: PortalTarget,
    error: Exception,
    *,
    report: ScrapeReport | None = None,
) -> dict[str, Any]:
    return {
        "portal": target.id,
        "target": target.id,
        "engine": target.engine,
        "domain": target.effective_domain,
        "pages_fetched": report.pages_fetched if report is not None else 0,
        "discovered": len(report.discovered) if report is not None else 0,
        "skipped_cpv": report.skipped_cpv if report is not None else 0,
        "skipped_invalid": report.skipped_invalid if report is not None else 0,
        "skipped_stale": report.skipped_stale if report is not None else 0,
        "errors": [*report.errors, str(error)] if report is not None else [str(error)],
        "results": [],
        "since": None,
        "error": str(error),
    }


async def run_scrape_once(
    portal: str,
    *,
    settings: Settings,
    repository: TenderRepository,
    pipeline: IngestionPipeline | None = None,
    audit: AuditService | None = None,
    storage: ObjectWriter | None = None,
    jobs: TenderIngestionJobRepository | None = None,
    states: ScraperStateRepository | None = None,
    runs: WorkerRunRepository | None = None,
    locks: PortalLocker | None = None,
    max_pages: int | None = None,
    scraper_factory: ScraperFactory | None = None,
    stop_event: asyncio.Event | None = None,
    worker_id: str | None = None,
    targets: Sequence[PortalTarget] | None = None,
    domain_limiter: DomainRateLimiter | None = None,
    domain_locker: DomainLocker | None = None,
    concurrency: int | None = None,
) -> list[dict[str, Any]]:
    """Scrape every selected target and persist results in source order.

    Network discovery runs in bounded batches. A slow portal therefore does not
    block the others, while attachment persistence and the outbox stay
    sequential on the caller's SQLAlchemy session. Each portal has its own
    PostgreSQL advisory lock and each HTTP domain a Redis-backed lock and
    spacing limiter, so replicas can scale out safely.
    """

    active_storage: ObjectWriter = storage if storage is not None else MinioObjectStorage(settings)
    active_worker = worker_id or worker_identity()
    chosen = _targets_for(portal, targets, settings=settings)
    parallelism = max(1, concurrency or settings.scraper_max_concurrent_portals)
    owns_limiter = domain_limiter is None
    owns_locker = domain_locker is None
    active_limiter = domain_limiter or build_domain_rate_limiter(settings)
    active_locker = domain_locker or build_domain_locker(settings)
    summaries: list[dict[str, Any]] = []
    failures: list[Exception] = []

    async def release_portal(name: str) -> None:
        if locks is not None:
            await locks.release(name)

    async def prepare(target: PortalTarget) -> _PreparedPortal | None:
        name = target.id
        acquired = await locks.acquire(name) if locks is not None else True
        if not acquired:
            await _record_locked(target, active_worker, runs, summaries)
            return None

        domain = target.effective_domain
        domain_acquired = await active_locker.acquire(domain)
        if not domain_acquired:
            logger.warning("domain %s is already being scraped by another worker", domain)
            await _record_locked(target, active_worker, runs, summaries, domain=domain)
            await release_portal(name)
            return None

        try:
            run = None
            if runs is not None:
                run = await runs.start(
                    run_type="scrape",
                    resource=name,
                    worker_id=active_worker,
                    details={"engine": target.engine, "domain": domain},
                )
            state = await states.get(name) if states is not None else None
            since = state.last_success_at if state is not None else None
            if states is not None:
                await states.mark_started(name)
            scraper = scraper_factory(name, settings, active_storage) if scraper_factory else None
            effective_max_pages = max_pages if max_pages is not None else target.max_pages
            return _PreparedPortal(
                target=target,
                max_pages=effective_max_pages,
                since=since,
                scraper=scraper,
                run_id=run.id if run is not None else None,
            )
        except Exception:
            await active_locker.release(domain)
            await release_portal(name)
            raise

    async def scrape(prepared: _PreparedPortal) -> None:
        target = prepared.target
        try:
            report: ScrapeReport
            if prepared.scraper is not None:
                report = await prepared.scraper.scrape(
                    max_pages=prepared.max_pages,
                    since=prepared.since,
                )
            elif settings.scraper_prefer_api and target.api is not None:
                try:
                    api_scraper = build_api_scraper(
                        target,
                        settings,
                        active_storage,
                        rate_limiter=active_limiter,
                    )
                    report = await api_scraper.scrape(
                        max_pages=prepared.max_pages,
                        since=prepared.since,
                    )
                    prepared.used_api = True
                except Exception as exc:
                    if not target.api.fallback_to_html:
                        raise
                    logger.warning(
                        "API feed failed for portal=%s; falling back to HTML: %s",
                        target.id,
                        exc,
                    )
                    html_scraper = build_engine_portal_scraper(
                        target,
                        settings,
                        active_storage,
                        rate_limiter=active_limiter,
                    )
                    report = await html_scraper.scrape(
                        max_pages=prepared.max_pages,
                        since=prepared.since,
                    )
                    report = replace(
                        report,
                        errors=(f"API feed failed, HTML fallback used: {exc}", *report.errors),
                    )
            else:
                scraper = build_engine_portal_scraper(
                    target,
                    settings,
                    active_storage,
                    rate_limiter=active_limiter,
                )
                report = await scraper.scrape(
                    max_pages=prepared.max_pages,
                    since=prepared.since,
                )
            prepared.report = report
        except Exception as exc:
            prepared.error = exc
        finally:
            await active_locker.release(target.effective_domain)

    async def persist(prepared: _PreparedPortal) -> None:
        target = prepared.target
        name = target.id
        if prepared.error is not None or prepared.report is None:
            error = prepared.error or RuntimeError("scrape produced no report")
            failures.append(error)
            if states is not None:
                await states.mark_failure(name, str(error))
            if runs is not None and prepared.run_id is not None:
                await _finish_run(
                    runs,
                    prepared.run_id,
                    status="failed",
                    errors_count=1,
                    error=str(error),
                    details={"portal": name},
                )
            summaries.append(_error_summary(target, error))
            await release_portal(name)
            return

        report = prepared.report
        bridge = PortalIngestionBridge(
            repository,
            pipeline=pipeline,
            audit=audit,
            jobs=jobs,
        )
        try:
            results = await bridge.ingest_many(list(report.discovered))
        except Exception as exc:
            failures.append(exc)
            if states is not None:
                await states.mark_failure(name, str(exc))
            if runs is not None and prepared.run_id is not None:
                await _finish_run(
                    runs,
                    prepared.run_id,
                    status="failed",
                    errors_count=1,
                    error=str(exc),
                    details={"portal": name},
                )
            summaries.append(_error_summary(target, exc, report=report))
            await release_portal(name)
            return

        if states is not None:
            await states.mark_success(name)
        summary = {
            "portal": name,
            "target": target.id,
            "engine": target.engine,
            "domain": target.effective_domain,
            "used_api": prepared.used_api,
            "pages_fetched": report.pages_fetched,
            "discovered": len(report.discovered),
            "skipped_cpv": report.skipped_cpv,
            "skipped_invalid": report.skipped_invalid,
            "skipped_stale": report.skipped_stale,
            "errors": list(report.errors),
            "results": [_result_dict(result) for result in results],
            "since": prepared.since.isoformat() if prepared.since is not None else None,
        }
        summaries.append(summary)
        if runs is not None and prepared.run_id is not None:
            await _finish_run(
                runs,
                prepared.run_id,
                status="succeeded",
                items_processed=len(results),
                errors_count=len(report.errors),
                details=summary,
            )
        await release_portal(name)

    try:
        for start in range(0, len(chosen), parallelism):
            if stop_event is not None and stop_event.is_set():
                logger.info("shutdown requested; skipping remaining portal scrapes")
                break
            prepared_batch: list[_PreparedPortal] = []
            for target in chosen[start : start + parallelism]:
                if stop_event is not None and stop_event.is_set():
                    break
                prepared = await prepare(target)
                if prepared is not None:
                    prepared_batch.append(prepared)
            if not prepared_batch:
                continue
            await asyncio.gather(*(scrape(prepared) for prepared in prepared_batch))
            for prepared in prepared_batch:
                await persist(prepared)
            if stop_event is not None and stop_event.is_set():
                logger.info("shutdown requested after in-flight portal batch")
                break
    finally:
        if owns_locker:
            await active_locker.aclose()
        if owns_limiter:
            await active_limiter.aclose()

    if failures:
        # One broken portal must not block the rest of a 100-target run or the
        # follow-up ingestion pass; errors are persisted in the summaries/logs.
        logger.error("%d portal scrape(s) failed", len(failures))
    return summaries


async def _run_once_with_db(
    portal: str,
    *,
    settings: Settings,
    pipeline: IngestionPipeline | None = None,
    max_pages: int | None = None,
    scraper_factory: ScraperFactory | None = None,
    stop_event: asyncio.Event | None = None,
    include_unverified: bool = False,
) -> list[dict[str, Any]]:
    storage = MinioObjectStorage(settings)
    factory = get_session_factory(settings)
    locker = DatabasePortalLock(get_engine(settings))
    targets = _load_targets(settings, enabled_only=not include_unverified)
    try:
        async with factory() as session:
            return await run_scrape_once(
                portal,
                settings=settings,
                repository=TenderRepository(session),
                pipeline=pipeline,
                audit=AuditService(session),
                storage=storage,
                jobs=TenderIngestionJobRepository(session),
                states=ScraperStateRepository(session),
                runs=WorkerRunRepository(session),
                locks=locker,
                max_pages=max_pages,
                scraper_factory=scraper_factory,
                stop_event=stop_event,
                targets=targets,
            )
    finally:
        await locker.release_all()
        await dispose_engine()


async def process_jobs_once(
    *,
    settings: Settings,
    limit: int = 10,
    worker_id: str | None = None,
    notifier: TelegramNotifier | None = None,
    stop_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    """Claim and process queued TenderIngestionJob rows.

    A shutdown request stops the loop after the current DeepSeek/Docling job
    finishes; unstarted claims are released back to ``pending``.
    """

    active_worker = worker_id or worker_identity()
    active_notifier = notifier if notifier is not None else TelegramNotifier(settings)
    summaries: list[dict[str, Any]] = []
    if stop_event is not None and stop_event.is_set():
        return summaries
    storage = MinioObjectStorage(settings)
    factory = get_session_factory(settings)
    try:
        async with factory() as session:
            jobs = TenderIngestionJobRepository(session)
            claimed = await jobs.claim_pending(limit=limit, worker_id=active_worker)
            if not claimed:
                return []
            runs = WorkerRunRepository(session)
            run = None
            try:
                run = await runs.start(
                    run_type="ingestion",
                    resource="outbox",
                    worker_id=active_worker,
                    details={"claimed": len(claimed)},
                )
            except Exception:  # pragma: no cover - metrics must not break processing
                logger.exception("could not record ingestion run start")
                try:
                    await session.rollback()
                except Exception:
                    logger.exception("could not roll back failed ingestion run start")
            processor = TenderIngestionProcessor(
                TenderRepository(session),
                jobs,
                storage,
                settings,
                llm=build_agent_context(settings).llm,
            )
            processed = 0
            errors = 0
            hard_failures = 0
            interrupted = False
            for job in claimed:
                if stop_event is not None and stop_event.is_set():
                    await jobs.release(job.id)
                    interrupted = True
                    summaries.append(
                        {
                            "job_id": str(job.id),
                            "tender_id": str(job.tender_id),
                            "status": "cancelled",
                        }
                    )
                    continue
                try:
                    status = await processor.process(job)
                except Exception as exc:
                    errors += 1
                    try:
                        job_status = await jobs.fail(job.id, str(exc))
                    except Exception:  # pragma: no cover - keep the worker alive
                        job_status = "failed"
                        logger.exception("could not persist failed ingestion job=%s", job.id)
                    if job_status == "failed":
                        hard_failures += 1
                        try:
                            await _notify_dead_letter(
                                active_notifier,
                                job=job,
                                attempts=job.attempts,
                                error=str(exc),
                            )
                        except Exception:  # pragma: no cover - alerting is best-effort
                            logger.exception("could not send dead-letter alert job=%s", job.id)
                    summaries.append(
                        {
                            "job_id": str(job.id),
                            "tender_id": str(job.tender_id),
                            "status": job_status,
                            "error": str(exc),
                        }
                    )
                else:
                    processed += 1
                    summaries.append(
                        {
                            "job_id": str(job.id),
                            "tender_id": str(job.tender_id),
                            "status": status,
                        }
                    )

            if hard_failures:
                terminal_status = "failed"
            elif interrupted:
                terminal_status = "cancelled"
            else:
                terminal_status = "succeeded"
            try:
                if run is not None:
                    await runs.finish(
                        run.id,
                        status=terminal_status,
                        items_processed=processed,
                        errors_count=errors,
                        error=(
                            f"{hard_failures} job(s) reached the dead-letter state" if hard_failures else None
                        ),
                        details={"outcomes": summaries},
                    )
            except Exception:  # pragma: no cover - metrics must not break processing
                logger.exception("could not record ingestion run")
    finally:
        await dispose_engine()
    return summaries


async def run_scheduled(
    *,
    settings: Settings,
    portal: str,
    cron: str | None = None,
    pipeline: IngestionPipeline | None = None,
    max_pages: int | None = None,
    max_runs: int | None = None,
    stop_event: asyncio.Event | None = None,
    scraper_factory: ScraperFactory | None = None,
    process_pending: bool = True,
    notifier: TelegramNotifier | None = None,
) -> int:
    """Run the scraper job forever on the configured cron schedule."""

    schedule = CronExpression.parse(
        cron or settings.scraper_schedule_cron,
        timezone=settings.app_timezone,
    )
    active_notifier = notifier if notifier is not None else TelegramNotifier(settings)
    active_worker = worker_identity()
    logger.info(
        "scheduler started portal=%s cron=%r timezone=%s worker=%s",
        portal,
        schedule.expression,
        settings.app_timezone,
        active_worker,
    )

    async def job() -> None:
        try:
            summaries = await _run_once_with_db(
                portal,
                settings=settings,
                pipeline=pipeline,
                max_pages=max_pages,
                scraper_factory=scraper_factory,
                stop_event=stop_event,
            )
            for summary in summaries:
                logger.info(
                    "scrape portal=%s pages=%s discovered=%s created=%s skipped_cpv=%s locked=%s",
                    summary["portal"],
                    summary["pages_fetched"],
                    summary["discovered"],
                    sum(1 for item in summary["results"] if item["created"]),
                    summary["skipped_cpv"],
                    summary.get("locked", False),
                )
            if process_pending:
                processed = await process_jobs_once(
                    settings=settings,
                    notifier=active_notifier,
                    stop_event=stop_event,
                )
                for item in processed:
                    logger.info(
                        "ingestion job=%s tender=%s status=%s",
                        item["job_id"],
                        item["tender_id"],
                        item["status"],
                    )
        except Exception as exc:
            logger.exception("scheduled scrape failed portal=%s", portal)
            try:
                await active_notifier.notify(
                    _scrape_failure_message(
                        portal=portal,
                        cron=schedule.expression,
                        worker_id=active_worker,
                        error=str(exc),
                    )
                )
            except Exception:  # pragma: no cover - alerting is best-effort
                logger.exception("could not send scheduled-scrape alert")
            raise

    return await run_scheduler(
        job,
        schedule,
        stop_event=stop_event,
        max_runs=max_runs,
    )


async def _process_loop(
    settings: Settings,
    limit: int,
    interval: float,
    *,
    stop_event: asyncio.Event | None = None,
    notifier: TelegramNotifier | None = None,
    process_once: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
) -> None:
    if interval <= 0:
        raise ValueError("process interval must be positive")
    active_notifier = notifier if notifier is not None else TelegramNotifier(settings)
    runner = process_once or process_jobs_once
    while stop_event is None or not stop_event.is_set():
        summaries = await runner(
            settings=settings,
            limit=limit,
            notifier=active_notifier,
            stop_event=stop_event,
        )
        for summary in summaries:
            logger.info(
                "ingestion job=%s tender=%s status=%s",
                summary["job_id"],
                summary["tender_id"],
                summary["status"],
            )
        if stop_event is not None and stop_event.is_set():
            break
        await wait_for_stop(interval, stop_event)
    logger.info("ingestion process loop stopped")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yasmina-worker",
        description="Run portal scrapers once or on a daily cron schedule.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape = subparsers.add_parser("scrape", help="run one scrape immediately")
    scrape.add_argument("--portal", default="all", help="target id from portals.yaml, or all")
    scrape.add_argument("--max-pages", type=int, default=None)
    scrape.add_argument(
        "--include-unverified",
        action="store_true",
        help="pilot disabled catalogue entries (does not affect the daily scheduler)",
    )
    scrape.add_argument("--json", action="store_true", dest="as_json")

    process = subparsers.add_parser("process", help="process queued ingestion jobs")
    process.add_argument("--limit", type=int, default=10)
    process.add_argument(
        "--interval",
        type=float,
        default=None,
        help="poll continuously with this many seconds between empty/complete polls",
    )
    process.add_argument("--json", action="store_true", dest="as_json")

    schedule = subparsers.add_parser("schedule", help="run the daily cron scheduler")
    schedule.add_argument("--portal", default="all", help="target id from portals.yaml, or all")
    schedule.add_argument("--cron", default=None, help="five-field cron expression")
    schedule.add_argument("--max-pages", type=int, default=None)
    schedule.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="stop after N executions (maintenance/testing)",
    )
    schedule.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_format=settings.log_format == "json")

    try:
        if args.command == "scrape":
            summaries = asyncio.run(
                _run_with_shutdown(
                    lambda stop: _run_once_with_db(
                        args.portal,
                        settings=settings,
                        max_pages=args.max_pages,
                        stop_event=stop,
                        include_unverified=args.include_unverified,
                    )
                )
            )
            if args.as_json:
                print(json.dumps(summaries, indent=2, ensure_ascii=False))
            else:
                for summary in summaries:
                    print(
                        f"{summary['portal']}: {summary['discovered']} discovered, "
                        f"{sum(1 for item in summary['results'] if item['created'])} new, "
                        f"{summary['skipped_cpv']} CPV-filtered"
                    )
            return 0

        if args.command == "process":
            if args.interval is not None:
                asyncio.run(
                    _run_with_shutdown(
                        lambda stop: _process_loop(
                            settings,
                            args.limit,
                            args.interval,
                            stop_event=stop,
                        )
                    )
                )
                return 0
            summaries = asyncio.run(
                _run_with_shutdown(
                    lambda stop: process_jobs_once(
                        settings=settings,
                        limit=args.limit,
                        stop_event=stop,
                    )
                )
            )
            if args.as_json:
                print(json.dumps(summaries, indent=2, ensure_ascii=False))
            else:
                for summary in summaries:
                    print(f"{summary['job_id']}: {summary['status']}")
            return 0

        runs = asyncio.run(
            _run_with_shutdown(
                lambda stop: run_scheduled(
                    settings=settings,
                    portal=args.portal,
                    cron=args.cron,
                    max_pages=args.max_pages,
                    max_runs=args.max_runs,
                    stop_event=stop,
                )
            )
        )
        if args.as_json:
            print(json.dumps({"runs": runs}, ensure_ascii=False))
        return 0
    except (CronScheduleError, ValueError) as exc:
        print(f"worker configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logger.exception("worker run failed")
        print(f"worker run failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
