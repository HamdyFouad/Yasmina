"""Tender upload, pgvector matching, pricing and workflow endpoints."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.agents.graph import run_agent
from src.agents.nodes import price_offer
from src.agents.state import initial_state
from src.api.context import build_agent_context
from src.api.deps import (
    get_app_settings,
    get_audit_service,
    get_clamav_scanner,
    get_object_storage,
    get_session,
    get_tender_repository,
    require_roles,
)
from src.api.schemas import (
    MatchPayload,
    PricePayload,
    WorkflowPayload,
    parse_customer_offer,
    parse_supplier_quotes,
)
from src.api.security import Principal, Role
from src.config import Settings
from src.integrations.clamav_client import ClamAVError, ClamAVScanner
from src.integrations.minio_client import (
    MinioObjectStorage,
    ObjectStorageError,
    object_name_for_tender,
)
from src.models.enums import ProcurementProcedure, TenderSourceFormat
from src.models.schemas import CustomerOffer, Tender
from src.parsers.gaeb_parser import GaebParseError, parse_gaeb
from src.parsers.pdf_parser import PdfParseError, PdfParserUnavailable, parse_pdf
from src.repositories.tender_repository import (
    StoredTender,
    TenderRepository,
    TenderRepositoryError,
)
from src.services.audit_service import AuditService, AuditServiceError
from src.services.matching_service import HybridMatchingService, MatchResult, MatchingError

__all__ = ["router"]

router = APIRouter(prefix="/tenders", tags=["tenders"])

_GAEB_SUFFIXES = {".x83", ".d83"}


def _match_payload(result: MatchResult) -> dict[str, Any]:
    return {
        "article_number": result.candidate.article_number,
        "description": result.candidate.description,
        "score": result.score,
        "lexical_score": result.lexical_score,
        "semantic_score": result.semantic_score,
        "matched_attributes": list(result.matched_attributes),
        "reasons": list(result.reasons),
    }


async def _stored_with_document(
    stored: StoredTender,
    storage: MinioObjectStorage,
) -> StoredTender:
    if stored.document is not None or not stored.source_object:
        return stored
    suffix = Path(stored.source_filename or stored.source_object).suffix.lower()
    if suffix not in _GAEB_SUFFIXES:
        return stored

    try:
        data = await storage.get_bytes(stored.source_object)
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    tmp = Path(tempfile.gettempdir()) / f"{uuid4().hex}{suffix}"
    tmp.write_bytes(data)
    try:
        parsed = await run_in_threadpool(parse_gaeb, tmp)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"cannot rehydrate GAEB source: {exc}",
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return StoredTender(
        tender=stored.tender,
        document=parsed.document,
        source_filename=stored.source_filename,
        source_path=stored.source_path,
        source_object=stored.source_object,
    )


def _tender_or_404(tender_id: UUID, stored: StoredTender | None) -> StoredTender:
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tender not found")
    return stored


def _pdf_tender(filename: str, items: list[Any], warnings: list[str]) -> Tender:
    tender_id = uuid4()
    updated_items = [item.model_copy(update={"tender_id": tender_id}) for item in items]
    now = datetime.now(timezone.utc)
    return Tender(
        id=tender_id,
        tender_number=(Path(filename).stem or "PDF-TENDER")[:64],
        title=(Path(filename).stem or "PDF tender")[:512],
        client_name="Unbekannter Auftraggeber",
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
        source_format=TenderSourceFormat.PDF_DOCLING,
        submission_deadline=now + timedelta(days=30),
        items=updated_items,
        metadata={"source_file": filename, "pdf_warnings": warnings},
    )


def _export_target(settings: Settings, raw: str) -> Path:
    root = settings.gaeb_export_dir.resolve()
    candidate = Path(raw)
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="export_path must stay inside GAEB_EXPORT_DIR",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_tender(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_app_settings),
    repository: TenderRepository = Depends(get_tender_repository),
    scanner: ClamAVScanner = Depends(get_clamav_scanner),
    storage: MinioObjectStorage = Depends(get_object_storage),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.SALES, Role.ADMIN)),
) -> dict[str, Any]:
    """Scan, store in MinIO, parse and persist a GAEB/PDF tender."""

    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".x83", ".d83", ".pdf"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported file type; expected .x83, .d83 or .pdf",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")
    if len(data) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"upload exceeds {settings.max_upload_size_mb} MB",
        )

    upload_id = uuid4()
    object_name = object_name_for_tender(upload_id, filename)

    try:
        scan = await scanner.scan_bytes(data)
    except ClamAVError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"malware scanner unavailable: {exc}",
        ) from exc

    try:
        await audit.record_clamav(
            actor=principal.sub,
            upload_id=str(upload_id),
            filename=filename,
            clean=scan.clean,
            signature=scan.signature,
            object_name=object_name,
        )
    except AuditServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"audit log unavailable: {exc}",
        ) from exc

    if not scan.clean:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"malware detected: {scan.signature or 'unknown signature'}",
        )

    tmp = Path(tempfile.gettempdir()) / f"{uuid4().hex}{suffix}"
    tmp.write_bytes(data)
    warnings: list[str] = []
    document: Any = None
    try:
        if suffix == ".pdf":
            parsed_pdf = await run_in_threadpool(parse_pdf, tmp)
            tender = _pdf_tender(filename, list(parsed_pdf.items), list(parsed_pdf.warnings))
            warnings = list(parsed_pdf.warnings)
        else:
            parsed_gaeb = await run_in_threadpool(parse_gaeb, tmp)
            tender = parsed_gaeb.tender
            document = parsed_gaeb.document
            warnings = list(parsed_gaeb.warnings)
    except PdfParserUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (GaebParseError, PdfParseError, ValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)

    tender = tender.model_copy(update={"source_reference": object_name})
    content_type = "application/pdf" if suffix == ".pdf" else "application/xml"
    try:
        await storage.put_bytes(object_name, data, content_type)
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"object storage unavailable: {exc}",
        ) from exc

    try:
        stored = await repository.save(
            tender,
            source_filename=filename,
            source_object=object_name,
            document=document,
        )
    except TenderRepositoryError as exc:
        try:
            await storage.delete(object_name)
        except ObjectStorageError:
            pass
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return {
        "tender_id": str(stored.tender.id),
        "tender": stored.tender.model_dump(mode="json"),
        "object_name": object_name,
        "warnings": warnings,
    }


@router.post("/{tender_id}/match")
async def match_tender(
    tender_id: UUID,
    payload: MatchPayload,
    session: AsyncSession = Depends(get_session),
    repository: TenderRepository = Depends(get_tender_repository),
    settings: Settings = Depends(get_app_settings),
    _: Principal = Depends(require_roles(Role.SALES, Role.ADMIN)),
) -> dict[str, Any]:
    """Query catalogue embeddings from PostgreSQL and rank matches."""

    stored = _tender_or_404(tender_id, await repository.get(tender_id))
    if payload.query_embedding is not None and len(payload.query_embedding) != settings.embedding_dimensions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"query_embedding must have {settings.embedding_dimensions} dimensions",
        )

    matcher = HybridMatchingService()
    matches: dict[str, list[dict[str, Any]]] = {}
    try:
        for item in stored.tender.items:
            results = await matcher.match_database(
                session,
                item,
                query_embedding=payload.query_embedding,
                limit=payload.limit,
            )
            if results:
                matches[item.position_number] = [_match_payload(result) for result in results]
    except MatchingError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"catalogue database unavailable: {exc}",
        ) from exc

    return {"tender_id": str(tender_id), "matches": matches}


@router.post("/{tender_id}/price")
async def price_tender(
    tender_id: UUID,
    payload: PricePayload,
    repository: TenderRepository = Depends(get_tender_repository),
    storage: MinioObjectStorage = Depends(get_object_storage),
    settings: Settings = Depends(get_app_settings),
    _: Principal = Depends(require_roles(Role.SALES, Role.ADMIN)),
) -> dict[str, Any]:
    """Price stored tender positions against supplied supplier quotes."""

    stored = _tender_or_404(tender_id, await repository.get(tender_id))
    stored = await _stored_with_document(stored, storage)
    try:
        quotes = parse_supplier_quotes(payload.supplier_quotes)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    context = build_agent_context(settings)
    state = initial_state(
        tender=stored.tender,
        supplier_quotes=quotes,
        request=payload.offer_request.to_request(),
        gaeb_document=stored.document,
    )
    result = price_offer(state, context)
    offer = result.get("offer")
    if offer is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": result.get("errors", []), "warnings": result.get("warnings", [])},
        )
    return {
        "offer": offer.model_dump(mode="json"),
        "warnings": result.get("warnings", []),
    }


@router.post("/{tender_id}/workflow")
async def run_tender_workflow(
    tender_id: UUID,
    payload: WorkflowPayload,
    repository: TenderRepository = Depends(get_tender_repository),
    storage: MinioObjectStorage = Depends(get_object_storage),
    settings: Settings = Depends(get_app_settings),
    _: Principal = Depends(require_roles(Role.SALES, Role.ADMIN)),
) -> dict[str, Any]:
    """Run the LangGraph workflow; pending/approved status is returned as data."""

    stored = _tender_or_404(tender_id, await repository.get(tender_id))
    stored = await _stored_with_document(stored, storage)
    try:
        quotes = parse_supplier_quotes(payload.supplier_quotes)
        offer = parse_customer_offer(payload.offer) if payload.offer is not None else None
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    export_path = _export_target(settings, payload.export_path) if payload.export_path else None
    context = build_agent_context(settings)
    state = initial_state(
        tender=stored.tender,
        supplier_quotes=quotes,
        request=payload.offer_request.to_request(),
        offer=offer,
        gaeb_document=stored.document,
        export_path=str(export_path) if export_path else None,
    )
    result = run_agent(state, context)
    result_offer: CustomerOffer | None = result.get("offer")
    return {
        "status": result.get("status"),
        "offer": result_offer.model_dump(mode="json") if result_offer is not None else None,
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
        "exported_path": result.get("exported_path"),
    }
