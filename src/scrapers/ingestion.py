"""Persist discovered portal tenders and hand them to the ingestion workflow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.models.enums import AuditEventType
from src.models.schemas import Tender
from src.repositories.ingestion_repository import TenderIngestionJobRepository
from src.repositories.tender_repository import StoredTender, TenderRepository, TenderRepositoryError
from src.scrapers.base_scraper import BaseScraper, ScrapeReport
from src.scrapers.models import DiscoveredTender
from src.services.audit_service import AuditService, AuditServiceError

__all__ = [
    "IngestionPipeline",
    "PortalIngestionBridge",
    "PortalIngestionError",
    "PortalIngestionResult",
]


class PortalIngestionError(RuntimeError):
    """Raised when discovered metadata cannot be persisted or handed off."""


class IngestionPipeline(Protocol):
    """Queue/graph hand-off invoked after a tender row has been committed."""

    async def trigger(self, tender: Tender, *, stored: StoredTender) -> Any: ...


@dataclass(frozen=True, slots=True)
class PortalIngestionResult:
    tender_id: UUID
    tender_number: str
    created: bool
    pipeline_triggered: bool
    raw_object: str | None
    pipeline_result: Any = None
    job_id: UUID | None = None


class PortalIngestionBridge:
    """Bridge between scraper output, ``TenderRepository`` and the outbox."""

    def __init__(
        self,
        repository: TenderRepository,
        *,
        pipeline: IngestionPipeline | None = None,
        audit: AuditService | None = None,
        jobs: TenderIngestionJobRepository | None = None,
    ) -> None:
        self.repository = repository
        self.pipeline = pipeline
        self.audit = audit
        self.jobs = jobs

    async def ingest(self, discovered: DiscoveredTender) -> PortalIngestionResult:
        try:
            tender = discovered.to_tender()
        except Exception as exc:
            raise PortalIngestionError(f"cannot map discovered tender: {exc}") from exc

        existing = await self.repository.get_by_number(tender.tender_number)
        if existing is not None:
            return PortalIngestionResult(
                tender_id=existing.tender.id,
                tender_number=existing.tender.tender_number,
                created=False,
                pipeline_triggered=False,
                raw_object=existing.source_object,
            )

        job_view = None
        try:
            stored = await self.repository.save(
                tender,
                source_filename=f"{discovered.portal_id}.html",
                source_object=discovered.raw_object,
                commit=self.jobs is None,
            )
            if self.jobs is not None:
                job_view, _ = await self.jobs.enqueue(
                    stored.tender.id,
                    portal_name=discovered.portal,
                    attachments=[attachment.as_dict() for attachment in discovered.attachments],
                    raw_object=discovered.raw_object,
                    commit=False,
                )
                await self.repository.session.commit()
        except TenderRepositoryError as exc:
            raise PortalIngestionError(f"cannot persist portal tender: {exc}") from exc
        except Exception as exc:
            if self.jobs is not None:
                await self.repository.session.rollback()
            raise PortalIngestionError(f"cannot enqueue portal tender: {exc}") from exc

        if self.audit is not None:
            try:
                await self.audit.append(
                    event_type=AuditEventType.TENDER_INGESTED,
                    actor="portal_scraper",
                    entity_type="tender",
                    entity_id=str(stored.tender.id),
                    payload={
                        "portal": discovered.portal,
                        "portal_id": discovered.portal_id,
                        "tender_number": stored.tender.tender_number,
                        "cpv_codes": list(discovered.cpv_codes),
                        "raw_object": discovered.raw_object,
                        "attachment_count": len(discovered.attachments),
                        "job_id": str(job_view.id) if job_view is not None else None,
                    },
                )
            except AuditServiceError as exc:
                raise PortalIngestionError(f"cannot audit portal tender: {exc}") from exc

        pipeline_result: Any = None
        pipeline_triggered = False
        if self.jobs is None and self.pipeline is not None:
            try:
                pipeline_result = await self.pipeline.trigger(stored.tender, stored=stored)
                pipeline_triggered = True
            except Exception as exc:
                raise PortalIngestionError(f"ingestion pipeline rejected tender: {exc}") from exc

        return PortalIngestionResult(
            tender_id=stored.tender.id,
            tender_number=stored.tender.tender_number,
            created=True,
            pipeline_triggered=pipeline_triggered,
            raw_object=discovered.raw_object,
            pipeline_result=pipeline_result,
            job_id=job_view.id if job_view is not None else None,
        )

    async def ingest_many(
        self,
        discovered: Sequence[DiscoveredTender],
    ) -> list[PortalIngestionResult]:
        return [await self.ingest(item) for item in discovered]

    async def ingest_scraper(
        self,
        scraper: BaseScraper,
        *,
        max_pages: int | None = None,
        since: datetime | None = None,
    ) -> tuple[ScrapeReport, list[PortalIngestionResult]]:
        """Run discovery and persist every matched tender in one call."""

        report = await scraper.scrape(max_pages=max_pages, since=since)
        results = await self.ingest_many(list(report.discovered))
        return report, results
