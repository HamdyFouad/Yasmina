"""Live ClamAV integration: infected scraped attachments never reach a parser."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.config import Settings
from src.integrations.clamav_client import ClamAVScanner
from src.models.enums import AuditEventType, ProcurementProcedure, TenderSourceFormat
from src.models.schemas import Tender
from src.repositories.ingestion_repository import IngestionJobView
from src.repositories.tender_repository import StoredTender
from src.services.ingestion_processor import TenderIngestionProcessor

SERVICE_TESTS = os.getenv("YASMINA_TEST_SERVICES") == "1"
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

pytestmark = pytest.mark.skipif(
    not SERVICE_TESTS,
    reason="set YASMINA_TEST_SERVICES=1 to run the live ClamAV integration test",
)


class FakeRepository:
    def __init__(self, tender: Tender) -> None:
        self.tender = tender
        self.replaced = 0

    async def get(self, tender_id: UUID) -> StoredTender | None:
        return StoredTender(tender=self.tender) if tender_id == self.tender.id else None

    async def replace_items(self, *args: Any, **kwargs: Any) -> None:
        self.replaced += 1


class FakeJobs:
    def __init__(self) -> None:
        self.security_failed: list[tuple[UUID, str]] = []
        self.completed: list[UUID] = []

    async def complete(self, job_id: UUID) -> None:
        self.completed.append(job_id)

    async def skip(self, job_id: UUID, reason: str | None = None) -> None:
        return None

    async def fail(self, job_id: UUID, error: str, **kwargs: Any) -> None:
        return None

    async def security_fail(self, job_id: UUID, error: str) -> None:
        self.security_failed.append((job_id, error))


class FakeStorage:
    async def get_bytes(self, object_name: str) -> bytes:
        return EICAR


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


async def test_clamav_quarantines_eicar_from_scraped_attachment(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, app_env="test", database_url=None)
    tender = Tender(
        tender_number="BY-EICAR-1",
        title="Reinigung",
        client_name="Stadt Muster",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.PORTAL,
        submission_deadline=datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
        cpv_codes=["33760000-5"],
    )
    job = IngestionJobView(
        id=uuid4(),
        tender_id=tender.id,
        portal_name="vergabe.bayern",
        status="processing",
        attempts=1,
        max_attempts=5,
        attachments=(
            {
                "object_name": "scrapers/test/attachments/eicar.pdf",
                "filename": "eicar.pdf",
                "content_type": "application/pdf",
                "kind": "pdf",
            },
        ),
        raw_object=None,
    )
    jobs = FakeJobs()
    audit = FakeAudit()
    parsed: list[str] = []
    processor = TenderIngestionProcessor(
        FakeRepository(tender),  # type: ignore[arg-type]
        jobs,  # type: ignore[arg-type]
        FakeStorage(),
        settings,
        scanner=ClamAVScanner(
            settings.clamav_host,
            settings.clamav_port,
            timeout=settings.clamav_timeout_seconds,
        ),
        audit=audit,  # type: ignore[arg-type]
        work_dir=workspace_tmp,
        pdf_parser=lambda path: parsed.append("pdf"),
        gaeb_parser=lambda path: parsed.append("gaeb"),
    )

    status = await processor.process(job)

    assert status == "rejected"
    assert parsed == []
    assert jobs.security_failed
    assert jobs.completed == []
    assert "Eicar" in jobs.security_failed[0][1] or "EICAR" in jobs.security_failed[0][1]
    assert audit.events[0]["event_type"] is AuditEventType.SYSTEM_SECURITY_ALERT
