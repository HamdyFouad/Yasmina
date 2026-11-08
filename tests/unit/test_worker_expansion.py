"""Worker expansion tests: concurrent portal batches and API-first fallback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import src.worker as worker_module
from src.config import Settings
from src.models.enums import ProcurementProcedure
from src.models.schemas import Tender
from src.repositories.tender_repository import StoredTender
from src.scrapers.base_scraper import ScrapeReport
from src.scrapers.domain_limits import InMemoryDomainRateLimiter
from src.scrapers.models import DiscoveredTender
from src.scrapers.portal_config import PortalApiConfig, PortalTarget
from src.services.tender_dedup import tender_fingerprint
from src.services.worker_lock import LocalDomainLock
from src.worker import run_scrape_once


class FakeStorage:
    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        return object_name


class Repo:
    def __init__(self) -> None:
        self.by_number: dict[str, StoredTender] = {}
        self.by_hash: dict[str, StoredTender] = {}
        self.saved = 0

    async def get_by_number(self, tender_number: str) -> StoredTender | None:
        return self.by_number.get(tender_number)

    async def get_by_dedup_hash(self, dedup_hash: str) -> StoredTender | None:
        return self.by_hash.get(dedup_hash)

    async def save(self, tender: Tender, **kwargs: Any) -> StoredTender:
        self.saved += 1
        stored = StoredTender(tender=tender)
        self.by_number[tender.tender_number] = stored
        self.by_hash[tender_fingerprint(tender)] = stored
        return stored


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "database_url": None,
        "scraper_request_delay_seconds": 0,
        "scraper_harvest_attachments": False,
    }
    values.update(overrides)
    return Settings(**values)


def make_target(
    target_id: str,
    domain: str,
    *,
    api: PortalApiConfig | None = None,
) -> PortalTarget:
    return PortalTarget(
        id=target_id,
        name=target_id,
        start_url=f"https://{domain}/vergabe",
        engine="standard",
        portal_name=target_id,
        portal_code=target_id[:4].upper(),
        domain=domain,
        api=api,
    )


def make_discovered(target: PortalTarget) -> DiscoveredTender:
    return DiscoveredTender(
        portal=target.portal_name,
        portal_code=target.portal_code,
        portal_id="2026-001",
        title="Reinigung von Verwaltungsgebäuden",
        client_name="Stadt München",
        detail_url=f"https://{target.effective_domain}/ausschreibung/2026-001",
        submission_deadline=datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
        cpv_codes=("33760000-5",),
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
    )


def report(target: PortalTarget) -> ScrapeReport:
    return ScrapeReport(
        portal=target.portal_name,
        discovered=(make_discovered(target),),
        pages_fetched=1,
        skipped_cpv=0,
        skipped_invalid=0,
        errors=(),
    )


async def test_distinct_domains_scrape_concurrently() -> None:
    targets = [
        make_target("alpha", "alpha.example"),
        make_target("beta", "beta.example"),
    ]
    state = {"started": 0}
    both_started = asyncio.Event()
    release = asyncio.Event()

    class SlowScraper:
        def __init__(self, target: PortalTarget) -> None:
            self.target = target

        async def scrape(self, *, max_pages: int | None = None, since: object | None = None) -> Any:
            state["started"] += 1
            if state["started"] == 2:
                both_started.set()
            await release.wait()
            return report(self.target)

    def factory(name: str, settings: Settings, storage: object) -> SlowScraper:
        return SlowScraper(next(target for target in targets if target.id == name))

    repository = Repo()
    task = asyncio.create_task(
        run_scrape_once(
            "all",
            settings=make_settings(),
            repository=repository,  # type: ignore[arg-type]
            storage=FakeStorage(),
            targets=targets,
            scraper_factory=factory,  # type: ignore[arg-type]
            concurrency=2,
            domain_locker=LocalDomainLock(),
            domain_limiter=InMemoryDomainRateLimiter(),
        )
    )

    await asyncio.wait_for(both_started.wait(), timeout=2)
    release.set()
    summaries = await task

    assert state["started"] == 2
    assert [summary["portal"] for summary in summaries] == ["alpha", "beta"]
    assert repository.saved == 1  # same fingerprint: the second portal is deduplicated


async def test_api_is_preferred_and_fallback_marks_the_summary(monkeypatch: Any) -> None:
    api = PortalApiConfig(
        type="ted",
        url="https://api.example.test/notices",
        format="json",
        fallback_to_html=True,
    )
    target = make_target("api-portal", "api-portal.example", api=api)
    repository = Repo()
    calls: list[str] = []

    class ApiScraper:
        async def scrape(self, **kwargs: Any) -> ScrapeReport:
            calls.append("api")
            return report(target)

    class HtmlScraper:
        async def scrape(self, **kwargs: Any) -> ScrapeReport:
            calls.append("html")
            return report(target)

    monkeypatch.setattr(worker_module, "build_api_scraper", lambda *args, **kwargs: ApiScraper())
    monkeypatch.setattr(worker_module, "build_engine_portal_scraper", lambda *args, **kwargs: HtmlScraper())

    summaries = await run_scrape_once(
        "all",
        settings=make_settings(),
        repository=repository,  # type: ignore[arg-type]
        storage=FakeStorage(),
        targets=[target],
        concurrency=1,
        domain_locker=LocalDomainLock(),
        domain_limiter=InMemoryDomainRateLimiter(),
    )

    assert calls == ["api"]
    assert summaries[0]["used_api"] is True


async def test_api_failure_falls_back_to_html(monkeypatch: Any) -> None:
    api = PortalApiConfig(
        type="ted",
        url="https://api.example.test/notices",
        format="json",
        fallback_to_html=True,
    )
    target = make_target("api-portal", "api-portal.example", api=api)
    repository = Repo()
    calls: list[str] = []

    class BrokenApi:
        async def scrape(self, **kwargs: Any) -> ScrapeReport:
            calls.append("api")
            raise RuntimeError("feed offline")

    class HtmlScraper:
        async def scrape(self, **kwargs: Any) -> ScrapeReport:
            calls.append("html")
            return report(target)

    monkeypatch.setattr(worker_module, "build_api_scraper", lambda *args, **kwargs: BrokenApi())
    monkeypatch.setattr(worker_module, "build_engine_portal_scraper", lambda *args, **kwargs: HtmlScraper())

    summaries = await run_scrape_once(
        "all",
        settings=make_settings(),
        repository=repository,  # type: ignore[arg-type]
        storage=FakeStorage(),
        targets=[target],
        concurrency=1,
        domain_locker=LocalDomainLock(),
        domain_limiter=InMemoryDomainRateLimiter(),
    )

    assert calls == ["api", "html"]
    assert summaries[0]["used_api"] is False
    assert any("HTML fallback used" in error for error in summaries[0]["errors"])
