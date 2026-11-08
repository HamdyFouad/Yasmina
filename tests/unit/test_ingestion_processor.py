"""Background ingestion processor tests with injected parsers and storage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.agents.deepseek import TenderTriage
from src.config import Settings
from src.integrations.clamav_client import ClamAVError, ClamAVResult
from src.models.enums import AuditEventType, ProcurementProcedure, TenderSourceFormat, UnitOfMeasure
from src.models.schemas import Tender, TenderItem
from src.repositories.ingestion_repository import IngestionJobView
from src.repositories.tender_repository import StoredTender
from src.services.ingestion_processor import IngestionProcessingError, TenderIngestionProcessor

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self, tender: Tender) -> None:
        self.tender = tender
        self.replaced: list[tuple[UUID, list[TenderItem], dict[str, Any] | None]] = []

    async def get(self, tender_id: UUID) -> StoredTender | None:
        return StoredTender(tender=self.tender) if tender_id == self.tender.id else None

    async def replace_items(
        self,
        tender_id: UUID,
        items: list[TenderItem],
        *,
        metadata_updates: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> StoredTender:
        self.replaced.append((tender_id, items, metadata_updates))
        return StoredTender(tender=self.tender.model_copy(update={"items": items}))


class FakeJobs:
    def __init__(self) -> None:
        self.completed: list[UUID] = []
        self.skipped: list[tuple[UUID, str | None]] = []
        self.failed: list[tuple[UUID, str]] = []
        self.security_failed: list[tuple[UUID, str]] = []

    async def complete(self, job_id: UUID) -> None:
        self.completed.append(job_id)

    async def skip(self, job_id: UUID, reason: str | None = None) -> None:
        self.skipped.append((job_id, reason))

    async def fail(self, job_id: UUID, error: str, **kwargs: Any) -> None:
        self.failed.append((job_id, error))

    async def security_fail(self, job_id: UUID, error: str) -> None:
        self.security_failed.append((job_id, error))


class FakeScanner:
    def __init__(
        self,
        *,
        clean: bool = True,
        signature: str | None = None,
        raw: str = "stream: OK",
    ) -> None:
        self.clean = clean
        self.signature = signature
        self.raw = raw
        self.scanned: list[bytes] = []

    async def scan_bytes(self, data: bytes) -> ClamAVResult:
        self.scanned.append(data)
        return ClamAVResult(clean=self.clean, signature=self.signature, raw=self.raw)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class FakeStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def get_bytes(self, object_name: str) -> bytes:
        return self.objects[object_name]


class FakeLLM:
    def __init__(self, relevant: bool = True) -> None:
        self.relevant = relevant
        self.triaged: list[str] = []

    def triage_tender(self, text: str, *, cpv_codes: list[str] | None = None) -> TenderTriage:
        self.triaged.append(text)
        return TenderTriage(is_relevant=self.relevant, confidence=0.9, reason="test")

    def extract_item_specs(self, text: str) -> Any:
        return SimpleNamespace(
            material="Nitril", color="blau", glove_size="M", thickness=None, pack_size=None
        )


def make_tender() -> Tender:
    return Tender(
        tender_number="BY-2026-100",
        title="Reinigung von Verwaltungsgebäuden",
        client_name="Stadt Muster",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.PORTAL,
        submission_deadline=NOW,
        cpv_codes=["33760000-5"],
    )


def make_job(tender_id: UUID, *, kind: str = "pdf") -> IngestionJobView:
    return IngestionJobView(
        id=uuid4(),
        tender_id=tender_id,
        portal_name="vergabe.bayern",
        status="processing",
        attempts=1,
        max_attempts=5,
        attachments=(
            {
                "object_name": "scrapers/test/attachments/file.pdf"
                if kind == "pdf"
                else "scrapers/test/attachments/file.x83",
                "filename": "file.pdf" if kind == "pdf" else "file.x83",
                "content_type": "application/pdf" if kind == "pdf" else "application/xml",
                "kind": kind,
            },
        ),
        raw_object="scrapers/test/listing.html",
    )


def make_item() -> TenderItem:
    return TenderItem(
        position_number="1.1",
        short_text="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
    )


def make_processor(
    workspace_tmp,  # type: ignore[no-untyped-def]
    tender: Tender,
    *,
    llm: Any | None = None,
    parser_items: list[TenderItem] | None = None,
    kind: str = "pdf",
    scanner: FakeScanner | None = None,
    audit: FakeAudit | None = None,
    parsed: list[str] | None = None,
) -> tuple[TenderIngestionProcessor, FakeRepository, FakeJobs]:
    repository = FakeRepository(tender)
    jobs = FakeJobs()
    job = make_job(tender.id, kind=kind)
    storage = FakeStorage({job.attachments[0]["object_name"]: b"attachment"})

    def pdf_parser(path):  # type: ignore[no-untyped-def]
        if parsed is not None:
            parsed.append("pdf")
        return SimpleNamespace(items=parser_items if parser_items is not None else [make_item()], warnings=[])

    def gaeb_parser(path):  # type: ignore[no-untyped-def]
        if parsed is not None:
            parsed.append("gaeb")
        return SimpleNamespace(
            tender=tender.model_copy(update={"items": parser_items or [make_item()]}), warnings=[]
        )

    processor = TenderIngestionProcessor(
        repository,  # type: ignore[arg-type]
        jobs,  # type: ignore[arg-type]
        storage,
        Settings(_env_file=None, database_url=None),
        llm=llm,
        scanner=scanner or FakeScanner(),  # type: ignore[arg-type]
        audit=audit or FakeAudit(),  # type: ignore[arg-type]
        work_dir=workspace_tmp,
        pdf_parser=pdf_parser,
        gaeb_parser=gaeb_parser,
    )
    return processor, repository, jobs


async def test_processor_parses_pdf_and_persists_items(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    tender = make_tender()
    processor, repository, jobs = make_processor(workspace_tmp, tender)

    status = await processor.process(make_job(tender.id))

    assert status == "completed"
    assert repository.replaced
    stored_items = repository.replaced[0][1]
    assert stored_items[0].position_number == "1.1"
    assert stored_items[0].tender_id == tender.id
    assert jobs.completed
    assert repository.replaced[0][2]["ingestion_status"] == "completed"


async def test_processor_runs_triage_and_skips_irrelevant(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    tender = make_tender()
    llm = FakeLLM(relevant=False)
    processor, repository, jobs = make_processor(workspace_tmp, tender, llm=llm)
    job = make_job(tender.id)

    status = await processor.process(job)

    assert status == "skipped"
    assert llm.triaged
    assert repository.replaced == []
    assert jobs.skipped[0][0] == job.id
    assert "not relevant" in (jobs.skipped[0][1] or "")


async def test_processor_enriches_parsed_items(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    tender = make_tender()
    llm = FakeLLM(relevant=True)
    item = make_item()
    processor, repository, _ = make_processor(workspace_tmp, tender, llm=llm, parser_items=[item])

    status = await processor.process(make_job(tender.id))

    assert status == "completed"
    enriched = repository.replaced[0][1][0]
    assert enriched.material.value == "NITRILE"
    assert enriched.color.value == "BLUE"


async def test_processor_fails_when_no_parseable_positions(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    tender = make_tender()
    processor, _, _ = make_processor(workspace_tmp, tender, parser_items=[])
    with pytest.raises(IngestionProcessingError, match="no parseable"):
        await processor.process(make_job(tender.id))


async def test_processor_quarantines_malware_before_parsing(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    tender = make_tender()
    scanner = FakeScanner(
        clean=False,
        signature="Eicar-Test-Signature",
        raw="stream: Win.Test.EICAR_HDB-1 FOUND",
    )
    audit = FakeAudit()
    parsed: list[str] = []
    processor, repository, jobs = make_processor(
        workspace_tmp,
        tender,
        scanner=scanner,
        audit=audit,
        parsed=parsed,
    )
    job = make_job(tender.id)

    status = await processor.process(job)

    assert status == "rejected"
    assert parsed == []
    assert repository.replaced == []
    assert jobs.completed == []
    assert jobs.security_failed
    failed_job_id, error = jobs.security_failed[0]
    assert failed_job_id == job.id
    assert "Eicar-Test-Signature" in error
    assert audit.events
    event = audit.events[0]
    assert event["event_type"] is AuditEventType.SYSTEM_SECURITY_ALERT
    assert event["entity_id"] == str(job.id)
    assert event["payload"]["signature"] == "Eicar-Test-Signature"
    assert event["payload"]["action"] == "quarantined"
    assert "attachment" in scanner.scanned[0].decode()


async def test_processor_fails_closed_when_clamav_is_unavailable(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    tender = make_tender()

    class BrokenScanner(FakeScanner):
        async def scan_bytes(self, data: bytes):  # type: ignore[no-untyped-def]
            raise ClamAVError("clamd connection refused")

    processor, repository, jobs = make_processor(
        workspace_tmp,
        tender,
        scanner=BrokenScanner(),  # type: ignore[arg-type]
    )

    with pytest.raises(IngestionProcessingError, match="scanner unavailable"):
        await processor.process(make_job(tender.id))
    assert repository.replaced == []
    assert jobs.completed == []
