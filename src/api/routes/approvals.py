"""Ed25519 approval verification, application and invoice artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
    get_app_settings,
    get_audit_service,
    get_erpnext_client,
    get_invoice_repository,
    get_object_storage,
    get_offer_repository,
    get_session,
    get_tender_repository,
    require_roles,
)
from src.api.invoices import InvoiceWiringError, generate_invoice_artifacts
from src.api.schemas import ApprovalPayload, VerifyPayload, parse_customer_offer
from src.api.security import Principal, Role
from src.config import Settings
from src.integrations.erpnext_client import ERPNextClient, ERPNextError
from src.integrations.minio_client import (
    MinioObjectStorage,
    ObjectStorageError,
    object_name_for_invoice,
)
from src.models.enums import AuditEventType
from src.models.schemas import CustomerOffer
from src.repositories.invoice_repository import InvoiceRepository
from src.repositories.offer_repository import (
    OfferRepository,
    OfferRepositoryConflict,
    OfferRepositoryError,
    StoredOffer,
)
from src.repositories.tender_repository import TenderRepository
from src.services.approval_service import ApprovalError, ApprovalService
from src.services.audit_service import AuditService, AuditServiceError

__all__ = ["router"]

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _service(settings: Settings) -> ApprovalService:
    service = ApprovalService(settings.ed25519_approval_public_key)
    if not service.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ED25519_APPROVAL_PUBLIC_KEY is not configured",
        )
    return service


def _assert_four_eyes(
    principal: Principal,
    approved: CustomerOffer,
    *,
    owner: str | None,
) -> None:
    """Prevent the creator/owner (or a sales owner) from approving the offer."""

    if not owner:
        return
    owner_key = owner.strip().casefold()
    principal_key = principal.sub.strip().casefold()
    approver_key = (approved.approved_by or "").strip().casefold()
    if principal_key == owner_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="four-eyes principle: the offer owner cannot approve their own offer",
        )
    if approver_key and approver_key == owner_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="four-eyes principle: the approver must differ from the offer owner",
        )


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
    audit: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_roles(Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """Verify, persist and complete an approval idempotently by offer number."""

    try:
        offer = parse_customer_offer(payload.offer)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    service = _service(settings)
    try:
        approved = service.approve(
            offer,
            payload.signature,
            approver=payload.approver,
            approved_at=payload.approved_at,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    existing = await repository.get_by_number(approved.offer_number)
    tender_owner: str | None = None
    if approved.tender_id is not None:
        tender = await tenders.get(approved.tender_id)
        if tender is not None:
            tender_owner = tender.tender.owner
    effective_owner = (
        (existing.offer.owner if existing is not None else None)
        or payload.owner
        or approved.owner
        or tender_owner
    )
    _assert_four_eyes(principal, approved, owner=effective_owner)

    if existing is not None:
        current = existing.offer
        if current.id != approved.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="offer_number is already persisted with a different offer id",
            )
        if (
            current.ed25519_approval_signature is not None
            and current.ed25519_approval_signature != approved.ed25519_approval_signature
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="offer_number is already approved with a different signature",
            )
        if (
            current.approval_payload_hash is not None
            and current.approval_payload_hash != approved.approval_payload_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="offer_number is already approved with a different payload",
            )
        approved = approved.model_copy(
            update={
                "id": current.id,
                "owner": current.owner or effective_owner or principal.sub,
                "created_at": current.created_at,
                "invoice_artifacts": dict(current.invoice_artifacts),
                "erpnext_quotation_name": current.erpnext_quotation_name,
            }
        )
    else:
        approved = approved.model_copy(update={"owner": effective_owner or principal.sub})

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
        try:
            quotation = await erpnext.create_quotation(persisted)
        except ERPNextError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"offer approved and persisted, but ERPNext quotation creation failed: {exc}",
            ) from exc

        persisted = persisted.model_copy(update={"erpnext_quotation_name": quotation.name})
        persisted = (await _persist(persisted)).offer
        erpnext_result = {
            "status": "created" if quotation.created else "existing",
            **quotation.as_dict(),
        }
        try:
            await audit.append(
                event_type=AuditEventType.ERP_QUOTATION_CREATED,
                actor=principal.sub,
                entity_type="customer_offer",
                entity_id=str(persisted.id),
                payload={
                    "offer_number": persisted.offer_number,
                    "quotation_name": quotation.name,
                    "created": quotation.created,
                    "approved_by": persisted.approved_by,
                },
                commit=False,
            )
            await session.commit()
        except AuditServiceError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"ERPNext quotation created but audit log is unavailable: {exc}",
            ) from exc
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
