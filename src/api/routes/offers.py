"""Customer-offer read endpoints with ownership-aware RBAC."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.api.deps import (
    get_app_settings,
    get_audit_service,
    get_object_storage,
    get_offer_repository,
    get_tender_repository,
    require_roles,
)
from src.api.security import Principal, Role
from src.config import Settings
from src.integrations.minio_client import MinioObjectStorage
from src.models.enums import AuditEventType, CustomerOfferStatus
from src.models.schemas import CustomerOffer, q2
from src.parsers.gaeb_parser import GaebExportError, write_x84
from src.repositories.offer_repository import (
    OfferRepository,
    OfferRepositoryConflict,
    OfferRepositoryError,
)
from src.repositories.tender_repository import TenderRepository
from src.services.approval_service import ApprovalService
from src.services.audit_service import AuditService, AuditServiceError
from src.services.tender_documents import TenderDocumentError, load_tender_document

__all__ = ["router"]

router = APIRouter(prefix="/offers", tags=["offers"])


def _is_sales_only(principal: Principal) -> bool:
    """True for pure sales users; approvers need the global offer view."""

    return principal.has_any((Role.SALES,)) and not principal.has_any(
        (Role.ADMIN, Role.VIEWER, Role.APPROVER)
    )


def _vat_breakdown(offer: CustomerOffer) -> list[dict[str, str]]:
    """Per-rate VAT bases computed from the lines with the server rounding rules."""

    by_rate: dict[Decimal, Decimal] = {}
    for line in offer.lines:
        by_rate[line.vat_rate] = by_rate.get(line.vat_rate, Decimal("0.00")) + line.total_net
    return [
        {
            "rate": str(rate),
            "taxable_amount": str(q2(base)),
            "tax_amount": str(q2(base * rate)),
        }
        for rate, base in sorted(by_rate.items())
    ]


@router.get("")
async def list_offers(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    offer_status: CustomerOfferStatus | None = Query(default=None, alias="status"),
    tender_id: UUID | None = Query(default=None),
    owner_filter: str | None = Query(default=None, alias="owner", min_length=1, max_length=128),
    repository: OfferRepository = Depends(get_offer_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN, Role.APPROVER)),
) -> dict[str, Any]:
    """List offers; pure sales users only see offers they own.

    Approvers, viewers and admins see every offer (the four-eyes rule is enforced
    on approval, not on reads). The ``owner`` filter is ignored for pure sales
    users, whose scope is always their own subject.
    """

    owner = principal.sub if _is_sales_only(principal) else owner_filter
    offers = await repository.list(
        limit=limit,
        offset=offset,
        owner=owner,
        status=offer_status,
        tender_id=tender_id,
    )
    total = await repository.count(owner=owner, status=offer_status, tender_id=tender_id)
    return {
        "offers": [offer.model_dump(mode="json") for offer in offers],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{offer_id}")
async def get_offer(
    offer_id: UUID,
    repository: OfferRepository = Depends(get_offer_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN, Role.APPROVER)),
) -> dict[str, Any]:
    """Read one offer; pure sales users may only read their own offers.

    The response adds a server-computed ``vat_breakdown`` so the dashboard never
    re-derives financial values from raw strings.
    """

    stored = await repository.get(offer_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
    if _is_sales_only(principal) and stored.offer.owner != principal.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sales users may only read their own offers",
        )
    return {
        "offer": stored.offer.model_dump(mode="json"),
        "vat_breakdown": _vat_breakdown(stored.offer),
    }


_APPROVED_STATUSES: frozenset[CustomerOfferStatus] = frozenset(
    {
        CustomerOfferStatus.APPROVED,
        CustomerOfferStatus.SENT,
        CustomerOfferStatus.ACCEPTED,
    }
)


def _ensure_offer_access(offer: CustomerOffer, principal: Principal) -> None:
    if _is_sales_only(principal) and offer.owner != principal.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sales users may only export their own offers",
        )


def _export_target(settings: Settings, offer: CustomerOffer) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", offer.offer_number).strip("-") or "offer"
    root = settings.gaeb_export_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe[:120]}.x84"


def _existing_export(settings: Settings, offer: CustomerOffer) -> Path | None:
    if not offer.gaeb_export_path:
        return None
    root = settings.gaeb_export_dir.resolve()
    candidate = Path(offer.gaeb_export_path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def _x84_response(path: Path) -> Response:
    return Response(
        content=path.read_bytes(),
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@router.get("/{offer_id}/x84")
async def download_x84_bid(
    offer_id: UUID,
    settings: Settings = Depends(get_app_settings),
    repository: OfferRepository = Depends(get_offer_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.APPROVER, Role.ADMIN)),
) -> Response:
    """Download an already-exported GAEB ``.x84`` bid for an approved offer."""

    stored = await repository.get(offer_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
    _ensure_offer_access(stored.offer, principal)
    path = _existing_export(settings, stored.offer)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="GAEB bid has not been exported for this offer",
        )
    return _x84_response(path)


@router.post("/{offer_id}/x84")
async def export_x84_bid(
    offer_id: UUID,
    settings: Settings = Depends(get_app_settings),
    storage: MinioObjectStorage = Depends(get_object_storage),
    repository: OfferRepository = Depends(get_offer_repository),
    tenders: TenderRepository = Depends(get_tender_repository),
    audit: AuditService = Depends(get_audit_service),
    principal: Principal = Depends(require_roles(Role.SALES, Role.APPROVER, Role.ADMIN)),
) -> Response:
    """Generate (or reuse) the approved offer's GAEB ``.x84`` bid and download it."""

    stored = await repository.get(offer_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
    offer = stored.offer
    _ensure_offer_access(offer, principal)

    existing = _existing_export(settings, offer)
    if existing is not None:
        return _x84_response(existing)

    if offer.status not in _APPROVED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only an approved offer can be exported as a GAEB bid",
        )
    if offer.tender_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="offer is not linked to a tender with a GAEB source",
        )

    tender = await tenders.get(offer.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source tender not found")
    try:
        hydrated = await load_tender_document(tender, storage)
    except TenderDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if hydrated.document is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a GAEB .x83/.d83 source document is required to export an .x84 bid",
        )

    service = ApprovalService(settings.ed25519_approval_public_key)
    if not service.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ED25519_APPROVAL_PUBLIC_KEY is not configured",
        )

    target = _export_target(settings, offer)
    try:
        write_x84(hydrated.document, offer, target, approval_service=service)
    except GaebExportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"GAEB X84 export failed: {exc}",
        ) from exc

    updated = offer.model_copy(update={"gaeb_export_path": str(target)})
    try:
        await repository.save(updated)
    except OfferRepositoryConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except OfferRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"GAEB exported but offer metadata could not be saved: {exc}",
        ) from exc

    try:
        await audit.append(
            event_type=AuditEventType.GAEB_EXPORTED,
            actor=principal.sub,
            entity_type="customer_offer",
            entity_id=str(offer.id),
            payload={
                "offer_number": offer.offer_number,
                "path": str(target),
                "filename": target.name,
            },
        )
    except AuditServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"GAEB exported but audit logging failed: {exc}",
        ) from exc

    return _x84_response(target)
