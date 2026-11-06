"""Catalogue seeding persistence for supplier products and embeddings."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.models.orm import CatalogArticle, Supplier

__all__ = ["CatalogRepository", "CatalogSeedItem", "CatalogSeedResult"]


@dataclass(slots=True)
class CatalogSeedItem:
    supplier_name: str
    article_number: str
    description: str
    material: MaterialType | None = None
    color: StandardizedColor | None = None
    glove_size: GloveSize = GloveSize.NOT_APPLICABLE
    unit: UnitOfMeasure | None = None
    unit_price_net: Decimal | None = None
    pack_size: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass(frozen=True, slots=True)
class CatalogSeedResult:
    suppliers: int
    upserted: int

    def as_dict(self) -> dict[str, int]:
        return {"suppliers": self.suppliers, "upserted": self.upserted}


class CatalogRepository:
    """Idempotent supplier/article upserts by ``(supplier, article_number)``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_supplier(self, name: str) -> Supplier:
        normalized = name.strip()
        if not normalized:
            raise ValueError("supplier name must not be empty")
        existing = (
            (await self.session.execute(select(Supplier).where(Supplier.name == normalized)))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        supplier = Supplier(name=normalized, is_active=True)
        self.session.add(supplier)
        await self.session.flush()
        return supplier

    async def upsert_articles(self, supplier_id: UUID, items: list[CatalogSeedItem]) -> int:
        if not items:
            return 0
        upserted = 0
        for item in items:
            values = {
                "supplier_id": supplier_id,
                "article_number": item.article_number,
                "description": item.description,
                "material": item.material,
                "color": item.color,
                "glove_size": item.glove_size,
                "unit": item.unit,
                "unit_price_net": item.unit_price_net,
                "pack_size": item.pack_size,
                "attributes": dict(item.attributes),
                "embedding": item.embedding,
            }
            statement = (
                insert(CatalogArticle)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        CatalogArticle.supplier_id,
                        CatalogArticle.article_number,
                    ],
                    set_={
                        "description": item.description,
                        "material": item.material,
                        "color": item.color,
                        "glove_size": item.glove_size,
                        "unit": item.unit,
                        "unit_price_net": item.unit_price_net,
                        "pack_size": item.pack_size,
                        "attributes": dict(item.attributes),
                        "embedding": item.embedding,
                        "updated_at": func.now(),
                    },
                )
                .returning(CatalogArticle.id)
            )
            result = await self.session.execute(statement)
            upserted += len(result.scalars().all())
        return upserted

    async def seed(self, items: list[CatalogSeedItem], *, commit: bool = True) -> CatalogSeedResult:
        grouped: dict[str, list[CatalogSeedItem]] = {}
        for item in items:
            grouped.setdefault(item.supplier_name, []).append(item)
        suppliers = 0
        upserted = 0
        for supplier_name, supplier_items in grouped.items():
            supplier = await self.get_or_create_supplier(supplier_name)
            suppliers += 1
            upserted += await self.upsert_articles(supplier.id, supplier_items)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return CatalogSeedResult(suppliers=suppliers, upserted=upserted)

    async def count(self) -> int:
        return int(
            (await self.session.execute(select(func.count()).select_from(CatalogArticle))).scalar_one()
        )
