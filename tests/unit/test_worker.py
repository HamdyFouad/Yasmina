"""Worker and scrape-summary tests (no live portal or database)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.models.enums import ProcurementProcedure
from src.models.schemas import Tender
from src.repositories.tender_repository import StoredTender
from src.scrapers.base_scraper import ScrapeReport
from src.scrapers.models import DiscoveredTender
from src.worker import build_parser, run_scrape_once


class FakeStorage:
    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        return object_name


class FakeRepository:
    def __init__(self) -> None:
        self.by_number: dict[str, StoredTender] = {}

    async def get_by_number(self, tender_number: str) -> StoredTender | None:
        return self.by_number.get(tender_number)

    async def save(
        self,
        tender: Tender,
        *,
        source_filename: str | None = None,
        source_path: object | None = None,
        source_object: str | None = None,
        document: object | None = None,
    ) -> StoredTender:
        stored = StoredTender(
            tender=tender,
            source_filename=source_filename,
            source_object=source_object,
        )
        self.by_number[tender.tender_number] = stored
        return stored


class FakePipeline:
    async def trigger(self, tender: Tender, *, stored: StoredTender) -> dict[str, str]:
        return {"queued": tender.tender_number}


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class FakeScraper:
    def __init__(self, discovered: list[DiscoveredTender]) -> None:
        self.discovered = discovered
        self.max_pages: int | None = None

    async def scrape(self, *, max_pages: int | None = None) -> ScrapeReport:
        self.max_pages = max_pages
        return ScrapeReport(
            portal="vergabe.bayern",
            discovered=tuple(self.discovered),
            pages_fetched=1,
            skipped_cpv=3,
            skipped_invalid=1,
            errors=("one detail page failed",),
        )


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        scraper_request_delay_seconds=0,
    )


def make_discovered(portal_id: str = "2026-001") -> DiscoveredTender:
    return DiscoveredTender(
        portal="vergabe.bayern",
        portal_code="BY",
        portal_id=portal_id,
        title="Reinigung von Verwaltungsgebäuden",
        client_name="Stadt München",
        detail_url=f"https://www.vergabe.bayern.de/ausschreibung/{portal_id}",
        submission_deadline=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
        cpv_codes=("33760000-5",),
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        raw_object="scrapers/vergabe.bayern/2026-01-15/abc.html",
    )


async def test_run_scrape_once_returns_structured_summary() -> None:
    repository = FakeRepository()
    pipeline = FakePipeline()
    audit = FakeAudit()
    fake_scraper = FakeScraper([make_discovered()])

    summaries = await run_scrape_once(
        "bayern",
        settings=make_settings(),
        repository=repository,  # type: ignore[arg-type]
        pipeline=pipeline,
        audit=audit,  # type: ignore[arg-type]
        storage=FakeStorage(),
        max_pages=2,
        scraper_factory=lambda portal, settings, storage: fake_scraper,  # type: ignore[arg-type]
    )

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["portal"] == "bayern"
    assert summary["discovered"] == 1
    assert summary["pages_fetched"] == 1
    assert summary["skipped_cpv"] == 3
    assert summary["skipped_invalid"] == 1
    assert summary["errors"] == ["one detail page failed"]
    assert summary["results"][0]["tender_number"] == "BY-2026-001"
    assert summary["results"][0]["created"] is True
    assert summary["results"][0]["pipeline_triggered"] is True
    assert fake_scraper.max_pages == 2
    assert audit.events[0]["event_type"].value == "TENDER_INGESTED"


async def test_run_scrape_once_supports_all_portals() -> None:
    repository = FakeRepository()
    calls: list[str] = []

    def factory(portal: str, settings: Settings, storage: object) -> FakeScraper:
        calls.append(portal)
        return FakeScraper([make_discovered(f"{portal}-001")])

    summaries = await run_scrape_once(
        "all",
        settings=make_settings(),
        repository=repository,  # type: ignore[arg-type]
        storage=FakeStorage(),
        scraper_factory=factory,
    )

    assert calls == ["bayern", "bund"]
    assert [summary["portal"] for summary in summaries] == ["bayern", "bund"]
    assert len(repository.by_number) == 2


def test_worker_parser_accepts_cron_and_portal() -> None:
    args = build_parser().parse_args(["schedule", "--portal", "bund", "--cron", "0 3 * * *", "--max-runs", "1"])

    assert args.command == "schedule"
    assert args.portal == "bund"
    assert args.cron == "0 3 * * *"
    assert args.max_runs == 1
