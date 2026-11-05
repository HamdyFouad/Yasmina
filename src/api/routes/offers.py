"""Customer-offer read endpoints with ownership-aware RBAC."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_offer_repository, require_roles
from src.api.security import Principal, Role
from src.models.enums import CustomerOfferStatus
from src.repositories.offer_repository import OfferRepository

__all__ = ["router"]

router = APIRouter(prefix="/offers", tags=["offers"])


def _is_sales_only(principal: Principal) -> bool:
    return principal.has_any((Role.SALES,)) and not principal.has_any((Role.ADMIN, Role.VIEWER))


def _owner_filter(principal: Principal) -> str | None:
    return principal.sub if _is_sales_only(principal) else None


@router.get("")
async def list_offers(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    offer_status: CustomerOfferStatus | None = Query(default=None, alias="status"),
    repository: OfferRepository = Depends(get_offer_repository),
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN)),
) -> dict[str, Any]:
    """List offers; sales users only see offers they own."""

    owner = _owner_filter(principal)
    offers = await repository.list(
        limit=limit,
        offset=offset,
        owner=owner,
        status=offer_status,
    )
    total = await repository.count(owner=owner, status=offer_status)
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
    principal: Principal = Depends(require_roles(Role.SALES, Role.VIEWER, Role.ADMIN)),
) -> dict[str, Any]:
    """Read one offer; sales users may only read their own offers."""

    stored = await repository.get(offer_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
    if _is_sales_only(principal) and stored.offer.owner != principal.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sales users may only read their own offers",
        )
    return {"offer": stored.offer.model_dump(mode="json")}
