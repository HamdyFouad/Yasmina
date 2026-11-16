"""Customer-offer read endpoints with ownership-aware RBAC."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_offer_repository, require_roles
from src.api.security import Principal, Role
from src.models.enums import CustomerOfferStatus
from src.models.schemas import CustomerOffer, q2
from src.repositories.offer_repository import OfferRepository

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
