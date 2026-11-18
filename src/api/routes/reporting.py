"""Reporting and analytics aggregates for the dashboard."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_reporting_repository, require_roles
from src.api.security import Principal, Role
from src.repositories.reporting_repository import ReportingRepository

router = APIRouter(prefix="/reporting", tags=["reporting"])

READ_ROLES = (Role.ADMIN, Role.SALES, Role.APPROVER, Role.VIEWER)


def _owner(principal: Principal) -> str | None:
    if principal.has_any((Role.SALES,)) and not principal.has_any((Role.ADMIN, Role.APPROVER, Role.VIEWER)):
        return principal.sub
    return None


@router.get("/summary")
async def reporting_summary(
    repository: ReportingRepository = Depends(get_reporting_repository),
    principal: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    return (await repository.summary(owner=_owner(principal))).as_dict()


@router.get("/margins")
async def reporting_margins(
    repository: ReportingRepository = Depends(get_reporting_repository),
    principal: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    data = await repository.margins(owner=_owner(principal))
    return {
        "customers": [row.as_dict() for row in data["customers"]],
        "tenders": [row.as_dict() for row in data["tenders"]],
        "suppliers": [row.as_dict() for row in data["suppliers"]],
    }


@router.get("/aging")
async def reporting_aging(
    today: date | None = Query(default=None),
    repository: ReportingRepository = Depends(get_reporting_repository),
    principal: Principal = Depends(require_roles(*READ_ROLES)),
) -> dict[str, object]:
    return (await repository.aging(owner=_owner(principal), today=today)).as_dict()
