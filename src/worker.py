"""CLI worker for one-off portal scrapes and daily cron scheduling."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Sequence
from typing import Any

from src.config import Settings, get_settings
from src.db import dispose_engine, get_session_factory
from src.integrations.minio_client import MinioObjectStorage
from src.repositories.tender_repository import TenderRepository
from src.scrapers.base_scraper import BaseScraper, ObjectWriter
from src.scrapers.bayern_vergabe import BayernVergabeScraper
from src.scrapers.ingestion import IngestionPipeline, PortalIngestionBridge, PortalIngestionResult
from src.scrapers.oeffentlichevergabe import OeffentlicheVergabeScraper
from src.scheduler import CronExpression, CronScheduleError, run_scheduler
from src.services.audit_service import AuditService

__all__ = [
    "PORTALS",
    "build_scraper",
    "main",
    "run_scheduled",
    "run_scrape_once",
]

logger = logging.getLogger("yasmina.worker")

PORTALS: dict[str, type[BaseScraper]] = {
    "bayern": BayernVergabeScraper,
    "bund": OeffentlicheVergabeScraper,
}
ALL_PORTALS = ("bayern", "bund")
ScraperFactory = Callable[[str, Settings, ObjectWriter], BaseScraper]


def build_scraper(portal: str, settings: Settings, storage: ObjectWriter) -> BaseScraper:
    try:
        scraper_type = PORTALS[portal]
    except KeyError as exc:
        raise ValueError(f"unknown portal {portal!r}; expected one of {sorted(PORTALS)}") from exc
    return scraper_type(settings, storage)


def _portal_names(portal: str) -> tuple[str, ...]:
    if portal == "all":
        return ALL_PORTALS
    if portal not in PORTALS:
        raise ValueError(f"unknown portal {portal!r}; expected one of all, {sorted(PORTALS)}")
    return (portal,)


def _result_dict(result: PortalIngestionResult) -> dict[str, Any]:
    return {
        "tender_id": str(result.tender_id),
        "tender_number": result.tender_number,
        "created": result.created,
        "pipeline_triggered": result.pipeline_triggered,
        "raw_object": result.raw_object,
    }


async def run_scrape_once(
    portal: str,
    *,
    settings: Settings,
    repository: TenderRepository,
    pipeline: IngestionPipeline | None = None,
    audit: AuditService | None = None,
    storage: ObjectWriter | None = None,
    max_pages: int | None = None,
    scraper_factory: ScraperFactory | None = None,
) -> list[dict[str, Any]]:
    """Run ``ingest_scraper`` for one portal (or ``all``)."""

    active_storage = storage if storage is not None else MinioObjectStorage(settings)
    factory = scraper_factory or build_scraper
    summaries: list[dict[str, Any]] = []
    for name in _portal_names(portal):
        scraper = factory(name, settings, active_storage)
        bridge = PortalIngestionBridge(repository, pipeline=pipeline, audit=audit)
        report, results = await bridge.ingest_scraper(scraper, max_pages=max_pages)
        summaries.append(
            {
                "portal": name,
                "pages_fetched": report.pages_fetched,
                "discovered": len(report.discovered),
                "skipped_cpv": report.skipped_cpv,
                "skipped_invalid": report.skipped_invalid,
                "errors": list(report.errors),
                "results": [_result_dict(result) for result in results],
            }
        )
    return summaries


async def _run_once_with_db(
    portal: str,
    *,
    settings: Settings,
    pipeline: IngestionPipeline | None = None,
    max_pages: int | None = None,
    scraper_factory: ScraperFactory | None = None,
) -> list[dict[str, Any]]:
    storage = MinioObjectStorage(settings)
    factory = get_session_factory(settings)
    try:
        async with factory() as session:
            return await run_scrape_once(
                portal,
                settings=settings,
                repository=TenderRepository(session),
                pipeline=pipeline,
                audit=AuditService(session),
                storage=storage,
                max_pages=max_pages,
                scraper_factory=scraper_factory,
            )
    finally:
        await dispose_engine()


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
) -> int:
    """Run the scraper job forever on the configured cron schedule."""

    schedule = CronExpression.parse(
        cron or settings.scraper_schedule_cron,
        timezone=settings.app_timezone,
    )
    logger.info(
        "scheduler started portal=%s cron=%r timezone=%s",
        portal,
        schedule.expression,
        settings.app_timezone,
    )

    async def job() -> None:
        summaries = await _run_once_with_db(
            portal,
            settings=settings,
            pipeline=pipeline,
            max_pages=max_pages,
            scraper_factory=scraper_factory,
        )
        for summary in summaries:
            logger.info(
                "scrape portal=%s pages=%s discovered=%s created=%s skipped_cpv=%s",
                summary["portal"],
                summary["pages_fetched"],
                summary["discovered"],
                sum(1 for item in summary["results"] if item["created"]),
                summary["skipped_cpv"],
            )

    return await run_scheduler(
        job,
        schedule,
        stop_event=stop_event,
        max_runs=max_runs,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yasmina-worker",
        description="Run portal scrapers once or on a daily cron schedule.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape = subparsers.add_parser("scrape", help="run one scrape immediately")
    scrape.add_argument("--portal", choices=("all", *PORTALS), default="all")
    scrape.add_argument("--max-pages", type=int, default=None)
    scrape.add_argument("--json", action="store_true", dest="as_json")

    schedule = subparsers.add_parser("schedule", help="run the daily cron scheduler")
    schedule.add_argument("--portal", choices=("all", *PORTALS), default="all")
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    settings = get_settings()

    try:
        if args.command == "scrape":
            summaries = asyncio.run(
                _run_once_with_db(
                    args.portal,
                    settings=settings,
                    max_pages=args.max_pages,
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

        runs = asyncio.run(
            run_scheduled(
                settings=settings,
                portal=args.portal,
                cron=args.cron,
                max_pages=args.max_pages,
                max_runs=args.max_runs,
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
