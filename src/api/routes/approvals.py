"""Ed25519 approval verification and application endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from src.api.deps import get_app_settings, require_roles
from src.api.schemas import ApprovalPayload, VerifyPayload, parse_customer_offer
from src.api.security import Principal, Role
from src.config import Settings
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
    _: Principal = Depends(require_roles(Role.APPROVER, Role.ADMIN)),
) -> dict[str, Any]:
    """Verify an externally produced signature and return the approved offer."""

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

    return {"offer": approved.model_dump(mode="json")}
