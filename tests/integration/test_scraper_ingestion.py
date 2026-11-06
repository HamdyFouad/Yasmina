"""Integration test for the scraper → TenderRepository → pipeline bridge."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from src.config import Settings
from src.models.enums import AuditEventType, ProcurementProcedure
from src.models.schemas import Tender
from src.repositories.ingestion_repository import IngestionJobView
from src.repositories.tender_repository import StoredTender
from src.scrapers.base_scraper import ScrapeReport
from src.scrapers.bayern_vergabe import BayernVergabeScraper
from src.scrapers.ingestion import PortalIngestionBridge
from src.scrapers.models import DiscoveredTender, PortalAttachment

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "portals"


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeRepository:
    def __init__(self) -> None:
        self.by_number: dict[str, StoredTender] = {}
        self.save_calls: list[dict[str, Any]] = []
        self.session = FakeSession()

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
        commit: bool = True,
    ) -> StoredTender:
        self.save_calls.append(
            {
                "tender": tender,
                "source_filename": source_filename,
                "source_object": source_object,
                "commit": commit,
            }
        )
        stored = StoredTender(
            tender=tender,
            source_filename=source_filename,
            source_object=source_object,
            document=document,
        )
        self.by_number[tender.tender_number] = stored
        return stored


class FakePipeline:
    def __init__(self) -> None:
        self.triggered: list[tuple[UUID, str | None]] = []

    async def trigger(self, tender: Tender, *, stored: StoredTender) -> dict[str, Any]:
        self.triggered.append((tender.id, stored.source_object))
        return {"queued": str(tender.id)}


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: list[IngestionJobView] = []
        self.attachments: list[dict[str, Any]] = []

    async def enqueue(self, tender_id, *, portal_name, attachments=(), raw_object=None, commit=True):  # type: ignore[no-untyped-def]
        self.attachments = list(attachments)
        job = IngestionJobView(
            id=uuid4(),
            tender_id=tender_id,
            portal_name=portal_name,
            status="pending",
            attempts=0,
            max_attempts=5,
            attachments=tuple(attachments),
            raw_object=raw_object,
        )
        self.jobs.append(job)
        return job, True


def make_discovered(portal_id: str = "2026-001") -> DiscoveredTender:
    return DiscoveredTender(
        portal="vergabe.bayern",
        portal_code="BY",
        portal_id=portal_id,
        title="Reinigung von Verwaltungsgebäuden",
        client_name="Stadt München",
        detail_url=f"https://www.vergabe.bayern.de/ausschreibung/{portal_id}",
        submission_deadline=datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
        publication_date=datetime(2026, 1, 15, tzinfo=UTC).date(),
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        cpv_codes=("33760000-5", "39830000-9"),
        raw_object="scrapers/vergabe.bayern/2026-01-15/abc.html",
        attachments=(
            PortalAttachment(
                url="https://www.vergabe.bayern.de/download/lv.pdf",
                filename="lv.pdf",
                object_name="scrapers/vergabe.bayern/attachments/hash.pdf",
                content_type="application/pdf",
                kind="pdf",
            ),
        ),
        metadata={"fixture": "integration"},
    )


async def test_bridge_persists_and_triggers_pipeline_once() -> None:
    repository = FakeRepository()
    pipeline = FakePipeline()
    audit = FakeAudit()
    bridge = PortalIngestionBridge(repository, pipeline=pipeline, audit=audit)
    discovered = make_discovered()

    first = await bridge.ingest(discovered)
    second = await bridge.ingest(discovered)

    assert first.created is True
    assert first.pipeline_triggered is True
    assert first.tender_number == "BY-2026-001"
    assert first.raw_object == discovered.raw_object
    assert first.pipeline_result == {"queued": str(first.tender_id)}
    assert second.created is False
    assert second.pipeline_triggered is False
    assert len(repository.save_calls) == 1
    assert len(pipeline.triggered) == 1
    assert repository.save_calls[0]["source_filename"] == "2026-001.html"
    assert repository.save_calls[0]["source_object"] == discovered.raw_object

    tender = repository.save_calls[0]["tender"]
    assert tender.source_format.value == "PORTAL"
    assert tender.status.value == "PUBLISHED"
    assert tender.cpv_codes == ["33760000-5", "39830000-9"]
    assert audit.entries[0]["event_type"] is AuditEventType.TENDER_INGESTED
    assert audit.entries[0]["entity_id"] == str(first.tender_id)


async def test_bridge_queues_outbox_instead_of_inline_pipeline() -> None:
    repository = FakeRepository()
    jobs = FakeJobRepository()
    pipeline = FakePipeline()
    audit = FakeAudit()
    bridge = PortalIngestionBridge(repository, pipeline=pipeline, audit=audit, jobs=jobs)
    discovered = make_discovered()

    result = await bridge.ingest(discovered)

    assert result.created is True
    assert result.pipeline_triggered is False
    assert result.job_id == jobs.jobs[0].id
    assert pipeline.triggered == []
    assert repository.save_calls[0]["commit"] is False
    assert repository.session.commits == 1
    assert jobs.attachments == [discovered.attachments[0].as_dict()]
    assert jobs.jobs[0].status == "pending"
    assert jobs.jobs[0].raw_object == discovered.raw_object
    assert audit.entries[0]["payload"]["job_id"] == str(jobs.jobs[0].id)


async def test_bridge_ingests_batch_and_keeps_sequences() -> None:
    repository = FakeRepository()
    pipeline = FakePipeline()
    bridge = PortalIngestionBridge(repository, pipeline=pipeline)

    results = await bridge.ingest_many([make_discovered("2026-001"), make_discovered("2026-002")])

    assert [result.tender_number for result in results] == ["BY-2026-001", "BY-2026-002"]
    assert all(result.created for result in results)
    assert len(pipeline.triggered) == 2


class FakeScraper:
    def __init__(self, discovered: list[DiscoveredTender]) -> None:
        self.discovered = discovered

    async def scrape(self, *, max_pages: int | None = None, since: object | None = None) -> ScrapeReport:
        return ScrapeReport(
            portal="vergabe.bayern",
            discovered=tuple(self.discovered),
            pages_fetched=1,
            skipped_cpv=2,
            skipped_invalid=0,
            errors=(),
        )


async def test_bridge_runs_discovery_and_persistence_together() -> None:
    repository = FakeRepository()
    pipeline = FakePipeline()
    bridge = PortalIngestionBridge(repository, pipeline=pipeline)
    scraper = FakeScraper([make_discovered("2026-010")])

    report, results = await bridge.ingest_scraper(scraper, max_pages=1)  # type: ignore[arg-type]

    assert report.pages_fetched == 1
    assert report.skipped_cpv == 2
    assert len(results) == 1
    assert results[0].created is True
    assert len(pipeline.triggered) == 1


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_bytes(self, object_name: str, data: bytes, content_type: str) -> str:
        self.objects[object_name] = data
        return object_name


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        scraper_request_delay_seconds=0,
        scraper_retry_backoff_seconds=0,
        scraper_user_agents=[],
        scraper_harvest_attachments=False,
    )


async def test_fixture_scrape_persists_and_triggers_pipeline_end_to_end() -> None:
    listing = (FIXTURES / "bayern_listing.html").read_text(encoding="utf-8")
    detail = (FIXTURES / "bayern_detail.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if "2026-003" in str(request.url):
            return httpx.Response(200, text=detail, headers={"Content-Type": "text/html"})
        return httpx.Response(200, text=listing, headers={"Content-Type": "text/html"})

    repository = FakeRepository()
    pipeline = FakePipeline()
    bridge = PortalIngestionBridge(repository, pipeline=pipeline)
    storage = FakeStorage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        scraper = BayernVergabeScraper(
            make_settings(),
            storage,
            client=http,
            user_agents=["YasminaTest/1.0"],
            start_url="https://portal.example/auftragsbekanntmachungen",
        )
        report, results = await bridge.ingest_scraper(scraper, max_pages=1)  # type: ignore[arg-type]

    assert report.skipped_cpv == 1
    assert [result.tender_number for result in results] == ["BY-2026-001", "BY-2026-003"]
    assert all(result.created for result in results)
    assert len(pipeline.triggered) == 2
    assert len(storage.objects) == 2  # listing and detail artifacts
    assert repository.by_number["BY-2026-001"].tender.source_format.value == "PORTAL"
