"""Admin catalogue override management for dashboard operators."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_catalog_repository, require_roles
from src.api.schemas import CatalogOverridePayload
from src.api.security import Principal, Role
from src.repositories.catalog_repository import CatalogRepository

__all__ = ["router"]

router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


@router.get("/overrides")
async def list_overrides(
    supplier: str | None = Query(default=None, max_length=256),
    article_number: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    repository: CatalogRepository = Depends(get_catalog_repository),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    """List articles that currently have pinned manual merge fields."""

    overrides, total = await repository.list_manual_overrides(
        supplier=supplier,
        article_number=article_number,
        limit=limit,
        offset=offset,
    )
    return {
        "overrides": [override.as_dict() for override in overrides],
        "count": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/overrides", status_code=status.HTTP_200_OK)
async def update_overrides(
    payload: CatalogOverridePayload,
    repository: CatalogRepository = Depends(get_catalog_repository),
    _: Principal = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    """Pin, replace or clear ``manual_overrides`` for one supplier article."""

    article = await repository.get_by_supplier_article(
        payload.supplier_name,
        payload.article_number,
    )
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="catalogue article not found",
        )
    try:
        if payload.mode == "clear":
            await repository.clear_manual_overrides(article.id, payload.fields or None)
        else:
            await repository.set_manual_overrides(
                article.id,
                payload.fields,
                mode=payload.mode,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    updated = await repository.get_by_supplier_article(
        payload.supplier_name,
        payload.article_number,
    )
    if updated is None:  # pragma: no cover - row cannot disappear under the update
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="catalogue article not found",
        )
    return {"override": updated.as_dict()}
