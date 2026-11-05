"""Ed25519 approval verification, application and invoice artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError

from src.api.deps import get_app_settings, get_object_storage, require_roles
from src.api.invoices import InvoiceWiringError, generate_invoice_artifacts
from src.api.schemas import ApprovalPayload, VerifyPayload, parse_customer_offer
from src.api.security import Principal, Role
from src.config import Settings
from src.integrations.minio_client import (
    MinioObjectStorage,
    ObjectStorageError,
    object_name_for_invoice,
)
from src.services.approval_service import ApprovalError, ApprovalService

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
    _: Principal = Depends(require_roles(Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """Verify an external signature, then generate and store e-invoice artifacts."""

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

    return {
        "offer": approved.model_dump(mode="json"),
        "artifacts": artifacts,
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
