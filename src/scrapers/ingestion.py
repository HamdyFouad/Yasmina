"""Persist discovered portal tenders and hand them to the ingestion workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from uuid import UUID

from src.models.enums import AuditEventType
from src.models.schemas import Tender
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


class PortalIngestionBridge:
    """Bridge between scraper output, ``TenderRepository`` and a pipeline hook."""

    def __init__(
        self,
        repository: TenderRepository,
        *,
        pipeline: IngestionPipeline | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.pipeline = pipeline
        self.audit = audit

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

        try:
            stored = await self.repository.save(
                tender,
                source_filename=f"{discovered.portal_id}.html",
                source_object=discovered.raw_object,
            )
        except TenderRepositoryError as exc:
            raise PortalIngestionError(f"cannot persist portal tender: {exc}") from exc

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
                    },
                )
            except AuditServiceError as exc:
                raise PortalIngestionError(f"cannot audit portal tender: {exc}") from exc

        pipeline_result: Any = None
        if self.pipeline is not None:
            try:
                pipeline_result = await self.pipeline.trigger(stored.tender, stored=stored)
            except Exception as exc:
                raise PortalIngestionError(f"ingestion pipeline rejected tender: {exc}") from exc

        return PortalIngestionResult(
            tender_id=stored.tender.id,
            tender_number=stored.tender.tender_number,
            created=True,
            pipeline_triggered=self.pipeline is not None,
            raw_object=discovered.raw_object,
            pipeline_result=pipeline_result,
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
    ) -> tuple[ScrapeReport, list[PortalIngestionResult]]:
        """Run discovery and persist every matched tender in one call."""

        report = await scraper.scrape(max_pages=max_pages)
        results = await self.ingest_many(list(report.discovered))
        return report, results
