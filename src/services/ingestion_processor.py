"""Background processor for queued portal tenders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.agents.nodes import enrich_specs
from src.agents.state import AgentContext
from src.config import Settings
from src.integrations.clamav_client import ClamAVError, ClamAVScanner
from src.models.enums import AuditEventType
from src.models.schemas import Tender, TenderItem
from src.parsers.gaeb_parser import GaebParseResult, parse_gaeb
from src.parsers.pdf_parser import parse_pdf
from src.repositories.ingestion_repository import (
    IngestionJobView,
    TenderIngestionJobRepository,
)
from src.repositories.tender_repository import TenderRepository, TenderRepositoryError
from src.services.approval_service import ApprovalService
from src.services.audit_service import AuditService, AuditServiceError
from src.services.unit_converter import UnitConverter

__all__ = ["IngestionProcessingError", "SecurityRejectedError", "TenderIngestionProcessor"]


class IngestionProcessingError(RuntimeError):
    """Raised when a queued tender cannot be processed."""


class SecurityRejectedError(IngestionProcessingError):
    """Raised when malware is detected in a downloaded attachment."""


class ObjectReader(Protocol):
    async def get_bytes(self, object_name: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class _AttachmentSpec:
    object_name: str
    filename: str
    content_type: str
    kind: str


@dataclass(frozen=True, slots=True)
class _ScannedAttachment:
    spec: _AttachmentSpec
    data: bytes


class TenderIngestionProcessor:
    """Download archived attachments, scan them and parse them."""

    def __init__(
        self,
        repository: TenderRepository,
        jobs: TenderIngestionJobRepository,
        storage: ObjectReader,
        settings: Settings,
        *,
        llm: Any | None = None,
        scanner: ClamAVScanner | None = None,
        audit: AuditService | None = None,
        work_dir: Path | None = None,
        pdf_parser: Callable[[Path], Any] = parse_pdf,
        gaeb_parser: Callable[[Path], Any] = parse_gaeb,
    ) -> None:
        self.repository = repository
        self.jobs = jobs
        self.storage = storage
        self.settings = settings
        self.llm = llm
        self.scanner = scanner or ClamAVScanner(
            settings.clamav_host,
            settings.clamav_port,
            timeout=settings.clamav_timeout_seconds,
        )
        self.audit = audit or AuditService(jobs.session)
        self.work_dir = work_dir or Path("var/ingestion")
        self.pdf_parser = pdf_parser
        self.gaeb_parser = gaeb_parser

    @staticmethod
    def _attachment_specs(job: IngestionJobView) -> list[_AttachmentSpec]:
        specs: list[_AttachmentSpec] = []
        for item in job.attachments:
            object_name = str(item.get("object_name", "")).strip()
            if not object_name:
                continue
            filename = str(item.get("filename") or Path(object_name).name)
            content_type = str(item.get("content_type") or "application/octet-stream")
            kind = str(item.get("kind") or "").casefold()
            if not kind:
                suffix = Path(filename).suffix.casefold()
                kind = (
                    "pdf" if suffix == ".pdf" else "gaeb" if suffix in {".x83", ".d83", ".x84"} else "other"
                )
            specs.append(_AttachmentSpec(object_name, filename, content_type, kind))
        return specs

    @staticmethod
    def _triage_text(tender: Tender, items: list[TenderItem]) -> str:
        positions = " ".join(
            f"{item.position_number} {item.short_text} {item.long_text or ''}".strip() for item in items
        )
        return (
            f"Title: {tender.title}\n"
            f"Client: {tender.client_name}\n"
            f"Description: {tender.description or ''}\n"
            f"CPV: {', '.join(tender.cpv_codes) or 'none'}\n"
            f"Positions: {positions}"
        )[:12000]

    def _triage(self, tender: Tender, items: list[TenderItem]) -> bool:
        if self.llm is None:
            return True
        method = getattr(self.llm, "triage_tender", None)
        if not callable(method):
            return True
        try:
            result = method(self._triage_text(tender, items), cpv_codes=list(tender.cpv_codes))
        except Exception:
            # A transient LLM failure must not discard the parsed documents.
            return True
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        return bool(data.get("is_relevant", True))

    def _enrich(self, tender: Tender, items: list[TenderItem]) -> tuple[list[TenderItem], list[str]]:
        if self.llm is None:
            return items, []
        state: dict[str, Any] = {"tender": tender.model_copy(update={"items": items}), "warnings": []}
        context = AgentContext(
            approval=ApprovalService(self.settings.ed25519_approval_public_key),
            converter=UnitConverter(),
            llm=self.llm,
        )
        result = enrich_specs(state, context)  # type: ignore[arg-type]
        enriched_tender = result.get("tender")
        if enriched_tender is None:
            return items, list(result.get("warnings", []))
        return list(enriched_tender.items), list(result.get("warnings", []))

    async def _handle_malware(
        self,
        job: IngestionJobView,
        spec: _AttachmentSpec,
        *,
        signature: str | None,
        raw: str,
    ) -> None:
        """Quarantine a job and append the tamper-evident security alert."""

        signature_text = signature or "unknown signature"
        error = f"malware detected in {spec.filename} ({signature_text})"
        try:
            await self.audit.append(
                event_type=AuditEventType.SYSTEM_SECURITY_ALERT,
                actor="ingestion_processor",
                entity_type="tender_ingestion_job",
                entity_id=str(job.id),
                payload={
                    "tender_id": str(job.tender_id),
                    "portal": job.portal_name,
                    "filename": spec.filename,
                    "object_name": spec.object_name,
                    "content_type": spec.content_type,
                    "signature": signature_text,
                    "clamav_response": raw[:500],
                    "action": "quarantined",
                },
            )
        except AuditServiceError as exc:
            raise IngestionProcessingError(
                f"cannot persist security alert for {spec.filename}: {exc}"
            ) from exc
        await self.jobs.security_fail(job.id, error)
        raise SecurityRejectedError(error)

    async def _download_and_scan(
        self,
        job: IngestionJobView,
        specs: list[_AttachmentSpec],
    ) -> tuple[list[_ScannedAttachment], list[str]]:
        scanned: list[_ScannedAttachment] = []
        warnings: list[str] = []
        if not specs:
            warnings.append("no PDF/GAEB attachments supplied")
        for spec in specs:
            if spec.kind not in {"pdf", "gaeb"}:
                warnings.append(f"{spec.filename}: unsupported attachment type")
                continue
            try:
                data = await self.storage.get_bytes(spec.object_name)
            except Exception as exc:
                warnings.append(f"{spec.filename}: cannot download from object storage: {exc}")
                continue
            try:
                result = await self.scanner.scan_bytes(data)
            except ClamAVError as exc:
                # Fail closed: never hand unscanned bytes to a parser.
                raise IngestionProcessingError(
                    f"malware scanner unavailable for {spec.filename}: {exc}"
                ) from exc
            if not result.clean:
                await self._handle_malware(
                    job,
                    spec,
                    signature=result.signature,
                    raw=result.raw,
                )
            scanned.append(_ScannedAttachment(spec=spec, data=data))
        return scanned, warnings

    async def _parse_attachments(
        self,
        tender: Tender,
        job: IngestionJobView,
        scanned: list[_ScannedAttachment],
        warnings: list[str],
    ) -> tuple[list[TenderItem], list[str]]:
        items: list[TenderItem] = []
        seen: set[tuple[str, str]] = set()
        if not scanned:
            return items, warnings

        self.work_dir.mkdir(parents=True, exist_ok=True)
        for attachment in scanned:
            spec = attachment.spec
            data = attachment.data
            suffix = Path(spec.filename).suffix or (".pdf" if spec.kind == "pdf" else ".x83")
            temp_path = self.work_dir / f"{job.id}-{Path(spec.object_name).stem}{suffix}"
            temp_path.write_bytes(data)
            try:
                if spec.kind == "pdf":
                    parsed = self.pdf_parser(temp_path)
                    parsed_items = list(getattr(parsed, "items", ()) or ())
                    warnings.extend(str(item) for item in getattr(parsed, "warnings", ()) or ())
                else:
                    parsed = self.gaeb_parser(temp_path)
                    parsed_tender = getattr(parsed, "tender", parsed)
                    parsed_items = list(getattr(parsed_tender, "items", ()) or ())
                    if isinstance(parsed, GaebParseResult):
                        warnings.extend(str(item) for item in parsed.warnings)
            except Exception as exc:
                warnings.append(f"{spec.filename}: parsing failed: {exc}")
                continue
            finally:
                temp_path.unlink(missing_ok=True)
            for item in parsed_items:
                key = (item.lot or "", item.position_number)
                if key in seen:
                    warnings.append(f"{spec.filename}: duplicate position {item.position_number!r}")
                    continue
                seen.add(key)
                items.append(item.model_copy(update={"tender_id": tender.id}))
        return items, warnings

    async def process(self, job: IngestionJobView) -> str:
        """Process one claimed job and update its outbox status."""

        stored = await self.repository.get(job.tender_id)
        if stored is None:
            await self.jobs.skip(job.id, "tender row disappeared")
            return "skipped"

        specs = self._attachment_specs(job)
        if job.attachments and not any(spec.kind in {"pdf", "gaeb"} for spec in specs):
            await self.jobs.skip(job.id, "no supported PDF/GAEB attachments")
            return "skipped"

        try:
            scanned, warnings = await self._download_and_scan(job, specs)
        except SecurityRejectedError:
            return "rejected"

        items, warnings = await self._parse_attachments(stored.tender, job, scanned, warnings)
        if not items and job.attachments:
            if warnings:
                raise IngestionProcessingError("; ".join(warnings))
            raise IngestionProcessingError("no parseable positions found in attachments")

        if not self._triage(stored.tender, items):
            await self.jobs.skip(job.id, "tender triaged as not relevant")
            return "skipped"

        if items:
            items, enrichment_warnings = self._enrich(stored.tender, items)
            warnings.extend(enrichment_warnings)
            try:
                await self.repository.replace_items(
                    stored.tender.id,
                    items,
                    metadata_updates={
                        "ingestion_status": "completed",
                        "ingestion_warnings": warnings,
                        "attachment_count": len(job.attachments),
                    },
                )
            except TenderRepositoryError as exc:
                raise IngestionProcessingError(f"cannot persist parsed tender items: {exc}") from exc
        else:
            await self.repository.replace_items(
                stored.tender.id,
                [],
                metadata_updates={
                    "ingestion_status": "completed",
                    "ingestion_warnings": warnings,
                },
            )

        await self.jobs.complete(job.id)
        return "completed"
