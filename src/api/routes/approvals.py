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
    get_object_storage,
    get_session,
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
    audit: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_roles(Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """Verify an external signature, then create e-invoice/ERPNext artefacts."""

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

    artifacts: dict[str, Any] | None = None
    if payload.invoice is not None:
        try:
            artifacts = await generate_invoice_artifacts(
                approved,
                settings,
                payload.invoice,
                storage,
            )
        except (InvoiceWiringError, ObjectStorageError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"offer approved but e-invoice generation failed: {exc}",
            ) from exc

    if erpnext.configured:
        try:
            quotation = await erpnext.create_quotation(approved)
        except ERPNextError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"offer approved but ERPNext quotation creation failed: {exc}",
            ) from exc

        approved = approved.model_copy(update={"erpnext_quotation_name": quotation.name})
        erpnext_result: dict[str, Any] = {
            "status": "created" if quotation.created else "existing",
            **quotation.as_dict(),
        }
        try:
            await audit.append(
                event_type=AuditEventType.ERP_QUOTATION_CREATED,
                actor=principal.sub,
                entity_type="customer_offer",
                entity_id=str(approved.id),
                payload={
                    "offer_number": approved.offer_number,
                    "quotation_name": quotation.name,
                    "created": quotation.created,
                    "approved_by": approved.approved_by,
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
        "offer": approved.model_dump(mode="json"),
        "artifacts": artifacts,
        "erpnext": erpnext_result,
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
