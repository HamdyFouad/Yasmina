"""Ed25519 approval verification, application and invoice artifacts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError

from src.api.deps import (
    get_app_settings,
    get_erpnext_client,
    get_invoice_repository,
    get_object_storage,
    get_offer_repository,
    get_quotation_sync_repository,
    get_tender_repository,
    require_roles,
)
from src.api.invoices import InvoiceWiringError, generate_invoice_artifacts
from src.api.schemas import ApprovalPayload, VerifyPayload, parse_customer_offer
from src.api.security import Principal, Role
from src.config import Settings
from src.integrations.erpnext_client import ERPNextClient
from src.integrations.minio_client import (
    MinioObjectStorage,
    ObjectStorageError,
    object_name_for_invoice,
)
from src.models.schemas import CustomerOffer
from src.repositories.invoice_repository import InvoiceRepository
from src.repositories.offer_repository import (
    OfferRepository,
    OfferRepositoryConflict,
    OfferRepositoryError,
    StoredOffer,
)
from src.repositories.quotation_sync_repository import QuotationSyncJobRepository
from src.repositories.tender_repository import TenderRepository
from src.services.approval_service import (
    ApprovalError,
    ApprovalService,
    approval_payload,
)

__all__ = ["router"]

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _service(settings: Settings) -> ApprovalService:
    private_key = settings.ed25519_approval_private_key
    service = ApprovalService(
        settings.ed25519_approval_public_key,
        private_key.get_secret_value() if private_key is not None else None,
    )
    if not service.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ED25519_APPROVAL_PUBLIC_KEY is not configured",
        )
    return service


def _signing_service(settings: Settings) -> ApprovalService:
    """Return the service that signs approvals with the server-held key."""

    service = _service(settings)
    if not service.can_sign:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ED25519_APPROVAL_PRIVATE_KEY is not configured; the server cannot sign approvals",
        )
    return service


def _assert_four_eyes(
    principal: Principal,
    offered_approver: str | None,
    *,
    owner: str | None,
) -> None:
    """Prevent the creator/owner (or a sales owner) from approving the offer."""

    if not owner:
        return
    owner_key = owner.strip().casefold()
    principal_key = principal.sub.strip().casefold()
    if principal_key == owner_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="four-eyes principle: the offer owner cannot approve their own offer",
        )
    approver_key = (offered_approver or "").strip().casefold()
    if approver_key and approver_key == owner_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="four-eyes principle: the approver must differ from the offer owner",
        )


def _sign(
    service: ApprovalService,
    offer: CustomerOffer,
    principal: Principal,
    approved_at: datetime | None,
) -> CustomerOffer:
    """Sign on behalf of the authenticated approver, mapping key errors to 422."""

    try:
        return service.sign(offer, approver=principal.sub, approved_at=approved_at)
    except ApprovalError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _replayed_approval(
    service: ApprovalService,
    *,
    current: CustomerOffer,
    candidate: CustomerOffer,
) -> CustomerOffer:
    """Return the stored approval when the same offer is submitted again.

    Re-signing would mint a new signature (and therefore a new payload hash) for
    an already-approved offer, so an identical replay short-circuits to the
    stored, verified state. Any content change under the same offer number is a
    conflict instead of a silent overwrite.
    """

    if current.approved_by is None or current.approved_at is None or not service.verify(current):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="stored approval signature is invalid; refusing to replay it",
        )
    try:
        stored_payload = approval_payload(
            current,
            approver=current.approved_by,
            approved_at=current.approved_at,
        )
        candidate_payload = approval_payload(
            candidate,
            approver=current.approved_by,
            approved_at=current.approved_at,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if stored_payload != candidate_payload:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="offer_number is already approved with a different payload",
        )
    return current


@router.post("/verify")
async def verify_approval(
    payload: VerifyPayload,
    settings: Settings = Depends(get_app_settings),
    _: Principal = Depends(require_roles(Role.VIEWER, Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """Verify a signed offer without mutating it."""

    try:
        offer = parse_customer_offer(payload.offer)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    valid = _service(settings).verify(offer)
    return {
        "offer_number": offer.offer_number,
        "valid": valid,
        "approved_by": offer.approved_by,
    }


@router.post("/approve")
async def approve_offer_endpoint(
    payload: ApprovalPayload,
    settings: Settings = Depends(get_app_settings),
    storage: MinioObjectStorage = Depends(get_object_storage),
    erpnext: ERPNextClient = Depends(get_erpnext_client),
    repository: OfferRepository = Depends(get_offer_repository),
    invoices: InvoiceRepository = Depends(get_invoice_repository),
    tenders: TenderRepository = Depends(get_tender_repository),
    sync_jobs: QuotationSyncJobRepository = Depends(get_quotation_sync_repository),
    principal: Principal = Depends(require_roles(Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """Sign, persist and complete an approval idempotently by offer number.

    The Ed25519 signature is produced by the server with the custody key for the
    authenticated ``approver``; clients never submit signatures. A repeated
    submission of an already-signed offer returns the stored approval unchanged.
    """

    try:
        offer = parse_customer_offer(payload.offer)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    service = _signing_service(settings)

    existing = await repository.get_by_number(offer.offer_number)
    tender_owner: str | None = None
    if offer.tender_id is not None:
        tender = await tenders.get(offer.tender_id)
        if tender is not None:
            tender_owner = tender.tender.owner
    effective_owner = (
        (existing.offer.owner if existing is not None else None)
        or payload.owner
        or offer.owner
        or tender_owner
    )
    _assert_four_eyes(principal, offer.approved_by, owner=effective_owner)

    if existing is not None:
        current = existing.offer
        if current.id != offer.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="offer_number is already persisted with a different offer id",
            )
        if current.ed25519_approval_signature:
            approved = _replayed_approval(service, current=current, candidate=offer)
        else:
            approved = _sign(service, offer, principal, payload.approved_at).model_copy(
                update={
                    "id": current.id,
                    "owner": current.owner or effective_owner or principal.sub,
                    "created_at": current.created_at,
                    "invoice_artifacts": dict(current.invoice_artifacts),
                    "erpnext_quotation_name": current.erpnext_quotation_name,
                }
            )
    else:
        approved = _sign(service, offer, principal, payload.approved_at).model_copy(
            update={"owner": effective_owner or principal.sub}
        )

    # Persist the cryptographically approved state before external side effects,
    # so a failed invoice/ERP call can be retried by submitting the same offer.
    async def _persist(value: CustomerOffer) -> StoredOffer:
        try:
            return await repository.save(value)
        except OfferRepositoryConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except OfferRepositoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"offer persistence failed: {exc}",
            ) from exc

    stored = await _persist(approved)

    persisted = stored.offer
    artifacts: dict[str, Any] | None = dict(persisted.invoice_artifacts) or None
    if payload.invoice is not None and not persisted.invoice_artifacts:
        try:
            artifacts = await generate_invoice_artifacts(
                persisted,
                settings,
                payload.invoice,
                storage,
                invoice_repository=invoices,
                actor=principal.sub,
            )
        except (InvoiceWiringError, ObjectStorageError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"offer approved and persisted, but e-invoice generation failed: {exc}",
            ) from exc
        persisted = persisted.model_copy(update={"invoice_artifacts": artifacts})
        persisted = (await _persist(persisted)).offer
        artifacts = dict(persisted.invoice_artifacts)

    if persisted.erpnext_quotation_name:
        erpnext_result: dict[str, Any] = {
            "status": "existing",
            "quotation_name": persisted.erpnext_quotation_name,
            "created": False,
        }
    elif erpnext.configured:
        # The external call is deliberately not made in the request path. A
        # durable outbox row is committed with the approval, and the worker
        # retries ERPNext with exponential backoff independently of the client.
        job, queued = await sync_jobs.enqueue(
            persisted.id,
            offer_number=persisted.offer_number,
            max_attempts=settings.quotation_sync_max_attempts,
        )
        erpnext_result = {
            "status": "queued",
            "job_id": str(job.id),
            "created": queued,
            "offer_number": persisted.offer_number,
        }
    else:
        erpnext_result = {
            "status": "skipped",
            "reason": "ERPNext API credentials are not configured",
        }

    return {
        "offer": persisted.model_dump(mode="json"),
        "artifacts": artifacts,
        "erpnext": erpnext_result,
        "created": stored.created,
    }


@router.get("/{offer_number}/documents/{document_format}")
async def download_invoice_document(
    offer_number: str,
    document_format: str,
    storage: MinioObjectStorage = Depends(get_object_storage),
    _: Principal = Depends(require_roles(Role.VIEWER, Role.APPROVER, Role.ADMIN)),
) -> Response:
    """Download the generated XRechnung XML or ZUGFeRD PDF for an offer."""

    formats = {
        "xrechnung": ("xml", "application/xml"),
        "zugferd": ("pdf", "application/pdf"),
    }
    if document_format not in formats:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown document format")
    extension, media_type = formats[document_format]
    object_name = object_name_for_invoice(offer_number, extension)

    try:
        if not await storage.exists(object_name):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
        data = await storage.get_bytes(object_name)
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    filename = f"{Path(object_name).parent.name}.{extension}"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
