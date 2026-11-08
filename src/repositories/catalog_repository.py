"""Catalogue seeding/merge persistence and embedding reindex queries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.models.orm import CatalogArticle, Supplier

__all__ = [
    "CatalogOverrideView",
    "CatalogRepository",
    "CatalogReindexItem",
    "CatalogSeedItem",
    "CatalogSeedResult",
    "ExistingArticle",
    "MERGEABLE_FIELDS",
    "dedupe_catalog_items",
    "merge_article_values",
]

# Fields a CSV/JSON import may update. Identity fields (supplier, article number)
# are immutable on purpose: changing them would create a new catalogue entry.
MERGEABLE_FIELDS = frozenset(
    {
        "description",
        "material",
        "color",
        "glove_size",
        "unit",
        "unit_price_net",
        "pack_size",
        "attributes",
        "embedding",
        "embedding_model",
    }
)


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
    embedding_model: str | None = None


@dataclass(frozen=True, slots=True)
class ExistingArticle:
    """Existing row state required to plan a merge."""

    id: UUID
    article_number: str
    description: str
    material: MaterialType | None
    color: StandardizedColor | None
    glove_size: GloveSize
    unit: UnitOfMeasure | None
    unit_price_net: Decimal | None
    pack_size: int | None
    attributes: dict[str, Any]
    manual_overrides: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogOverrideView:
    """One article and its manually pinned merge fields."""

    id: UUID
    supplier_id: UUID
    supplier_name: str
    article_number: str
    description: str
    material: MaterialType | None
    color: StandardizedColor | None
    glove_size: GloveSize
    unit: UnitOfMeasure | None
    unit_price_net: Decimal | None
    pack_size: int | None
    manual_overrides: tuple[str, ...]
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "supplier_id": str(self.supplier_id),
            "supplier_name": self.supplier_name,
            "article_number": self.article_number,
            "description": self.description,
            "material": self.material.value if self.material else None,
            "color": self.color.value if self.color else None,
            "glove_size": self.glove_size.value,
            "unit": self.unit.value if self.unit else None,
            "unit_price_net": format(self.unit_price_net, "f") if self.unit_price_net is not None else None,
            "pack_size": self.pack_size,
            "manual_overrides": list(self.manual_overrides),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CatalogSeedResult:
    suppliers: int
    created: int
    updated: int
    duplicates: int = 0

    @property
    def upserted(self) -> int:
        return self.created + self.updated

    def as_dict(self) -> dict[str, int]:
        return {
            "suppliers": self.suppliers,
            "created": self.created,
            "updated": self.updated,
            "upserted": self.upserted,
            "duplicates": self.duplicates,
        }


@dataclass(frozen=True, slots=True)
class CatalogReindexItem:
    id: UUID
    description: str
    material: MaterialType | None
    color: StandardizedColor | None
    glove_size: GloveSize
    unit: UnitOfMeasure | None
    pack_size: int | None
    attributes: dict[str, Any]
    manual_overrides: tuple[str, ...] = ()


def dedupe_catalog_items(items: list[CatalogSeedItem]) -> tuple[list[CatalogSeedItem], int]:
    """Collapse duplicate ``(supplier, article_number)`` rows, keeping the last."""

    ordered: dict[tuple[str, str], CatalogSeedItem] = {}
    duplicates = 0
    for item in items:
        key = (item.supplier_name.strip().casefold(), item.article_number.strip())
        if key in ordered:
            duplicates += 1
        ordered[key] = item
    return list(ordered.values()), duplicates


def _provided_updates(
    existing: ExistingArticle | None,
    item: CatalogSeedItem,
) -> dict[str, Any]:
    """Fields the import can write, ignoring fields the import did not supply."""

    provided: dict[str, Any] = {"description": item.description}
    if item.material is not None:
        provided["material"] = item.material
    if item.color is not None:
        provided["color"] = item.color
    if item.glove_size is not GloveSize.NOT_APPLICABLE:
        provided["glove_size"] = item.glove_size
    if item.unit is not None:
        provided["unit"] = item.unit
    if item.unit_price_net is not None:
        provided["unit_price_net"] = item.unit_price_net
    if item.pack_size is not None:
        provided["pack_size"] = item.pack_size
    if item.attributes:
        merged = dict(existing.attributes) if existing is not None else {}
        merged.update(item.attributes)
        provided["attributes"] = merged
    if item.embedding is not None:
        provided["embedding"] = item.embedding
        if item.embedding_model:
            provided["embedding_model"] = item.embedding_model
    return provided


def merge_article_values(
    existing: ExistingArticle | None,
    item: CatalogSeedItem,
    *,
    ignore_manual_overrides: bool = False,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Plan the update for an existing article.

    Returns the column values to write and the remaining manual-override list.
    Manual overrides win over imported values, and fields absent from the import
    never clear existing data.
    """

    provided = _provided_updates(existing, item)
    if existing is None:
        return provided, ()

    overrides = set() if ignore_manual_overrides else set(existing.manual_overrides)
    updates = {key: value for key, value in provided.items() if key not in overrides}
    remaining = tuple(existing.manual_overrides)
    if ignore_manual_overrides:
        # An explicit override-repair import clears the pins it actually wrote.
        remaining = tuple(name for name in existing.manual_overrides if name not in updates)
    return updates, remaining


def _existing_article(row: CatalogArticle) -> ExistingArticle:
    return ExistingArticle(
        id=row.id,
        article_number=row.article_number,
        description=row.description,
        material=row.material,
        color=row.color,
        glove_size=row.glove_size,
        unit=row.unit,
        unit_price_net=row.unit_price_net,
        pack_size=row.pack_size,
        attributes=dict(row.attributes or {}),
        manual_overrides=tuple(row.manual_overrides or ()),
    )


def _override_view(row: CatalogArticle, supplier_name: str) -> CatalogOverrideView:
    assert row.supplier_id is not None
    return CatalogOverrideView(
        id=row.id,
        supplier_id=row.supplier_id,
        supplier_name=supplier_name,
        article_number=row.article_number,
        description=row.description,
        material=row.material,
        color=row.color,
        glove_size=row.glove_size,
        unit=row.unit,
        unit_price_net=row.unit_price_net,
        pack_size=row.pack_size,
        manual_overrides=tuple(row.manual_overrides or ()),
        updated_at=row.updated_at,
    )


class CatalogRepository:
    """Merge-oriented upserts by ``(supplier, article_number)``."""

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

    async def _existing_articles(
        self,
        supplier_id: UUID,
        article_numbers: list[str],
    ) -> dict[str, ExistingArticle]:
        if not article_numbers:
            return {}
        rows = (
            (
                await self.session.execute(
                    select(CatalogArticle).where(
                        CatalogArticle.supplier_id == supplier_id,
                        CatalogArticle.article_number.in_(article_numbers),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.article_number: _existing_article(row) for row in rows}

    async def upsert_articles(
        self,
        supplier_id: UUID,
        items: list[CatalogSeedItem],
        *,
        ignore_manual_overrides: bool = False,
        now: datetime | None = None,
    ) -> tuple[int, int]:
        """Upsert one supplier's items, returning ``(created, updated)`` counts."""

        if not items:
            return 0, 0
        current = now or datetime.now(UTC)
        existing = await self._existing_articles(
            supplier_id,
            [item.article_number for item in items],
        )
        created = 0
        updated = 0
        for item in items:
            previous = existing.get(item.article_number)
            if previous is None:
                values: dict[str, Any] = {
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
                    "embedding_model": item.embedding_model,
                    "manual_overrides": [],
                    "last_imported_at": current,
                }
                created += 1
            else:
                updates, remaining = merge_article_values(
                    previous,
                    item,
                    ignore_manual_overrides=ignore_manual_overrides,
                )
                values = {
                    "supplier_id": supplier_id,
                    "article_number": item.article_number,
                    "description": previous.description,
                    **updates,
                    "manual_overrides": list(remaining),
                    "last_imported_at": current,
                }
                updated += 1

            statement = (
                insert(CatalogArticle)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        CatalogArticle.supplier_id,
                        CatalogArticle.article_number,
                    ],
                    set_={
                        key: value
                        for key, value in values.items()
                        if key not in {"supplier_id", "article_number"}
                    }
                    | {"updated_at": func.now()},
                )
            )
            await self.session.execute(statement)
        return created, updated

    async def seed(
        self,
        items: list[CatalogSeedItem],
        *,
        commit: bool = True,
        ignore_manual_overrides: bool = False,
        now: datetime | None = None,
    ) -> CatalogSeedResult:
        unique_items, duplicates = dedupe_catalog_items(items)
        grouped: dict[str, list[CatalogSeedItem]] = {}
        for item in unique_items:
            grouped.setdefault(item.supplier_name, []).append(item)
        created = 0
        updated = 0
        for supplier_name, supplier_items in grouped.items():
            supplier = await self.get_or_create_supplier(supplier_name)
            supplier_created, supplier_updated = await self.upsert_articles(
                supplier.id,
                supplier_items,
                ignore_manual_overrides=ignore_manual_overrides,
                now=now,
            )
            created += supplier_created
            updated += supplier_updated
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return CatalogSeedResult(
            suppliers=len(grouped),
            created=created,
            updated=updated,
            duplicates=duplicates,
        )

    async def set_manual_overrides(
        self,
        article_id: UUID,
        fields: list[str] | tuple[str, ...],
        *,
        mode: str = "add",
    ) -> tuple[str, ...]:
        """Pin or replace the fields a future import must not overwrite."""

        requested = list(dict.fromkeys(str(field).strip() for field in fields))
        unknown = [field for field in requested if field not in MERGEABLE_FIELDS]
        if unknown:
            raise ValueError(f"unknown catalogue field(s): {', '.join(sorted(unknown))}")
        row = await self.session.get(CatalogArticle, article_id)
        if row is None:
            raise ValueError(f"catalogue article {article_id} does not exist")
        if mode == "replace":
            overrides = requested
        elif mode == "add":
            overrides = list(dict.fromkeys([*(row.manual_overrides or []), *requested]))
        else:
            raise ValueError("mode must be 'add' or 'replace'")
        row.manual_overrides = overrides
        await self.session.commit()
        return tuple(overrides)

    async def clear_manual_overrides(
        self,
        article_id: UUID,
        fields: list[str] | tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        row = await self.session.get(CatalogArticle, article_id)
        if row is None:
            raise ValueError(f"catalogue article {article_id} does not exist")
        if fields is None:
            overrides: list[str] = []
        else:
            drop = set(fields)
            overrides = [field for field in (row.manual_overrides or []) if field not in drop]
        row.manual_overrides = overrides
        await self.session.commit()
        return tuple(overrides)

    async def reindex_page(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = 100,
        only_missing: bool = False,
    ) -> list[CatalogReindexItem]:
        statement = select(CatalogArticle)
        if only_missing:
            statement = statement.where(CatalogArticle.embedding.is_(None))
        if after_id is not None:
            statement = statement.where(CatalogArticle.id > after_id)
        rows = (
            (await self.session.execute(statement.order_by(CatalogArticle.id).limit(max(1, limit))))
            .scalars()
            .all()
        )
        return [
            CatalogReindexItem(
                id=row.id,
                description=row.description,
                material=row.material,
                color=row.color,
                glove_size=row.glove_size,
                unit=row.unit,
                pack_size=row.pack_size,
                attributes=dict(row.attributes or {}),
                manual_overrides=tuple(row.manual_overrides or ()),
            )
            for row in rows
        ]

    async def update_embeddings(
        self,
        rows: list[tuple[UUID, list[float], str]],
        *,
        now: datetime | None = None,
        commit: bool = True,
    ) -> int:
        current = now or datetime.now(UTC)
        for article_id, vector, model in rows:
            await self.session.execute(
                update(CatalogArticle)
                .where(CatalogArticle.id == article_id)
                .values(embedding=vector, embedding_model=model, updated_at=current)
            )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return len(rows)

    async def count_articles(self, *, only_missing: bool = False) -> int:
        statement = select(func.count()).select_from(CatalogArticle)
        if only_missing:
            statement = statement.where(CatalogArticle.embedding.is_(None))
        return int((await self.session.execute(statement)).scalar_one())

    async def count(self) -> int:
        return await self.count_articles()

    @staticmethod
    def _override_statement(
        *,
        supplier: str | None,
        article_number: str | None,
    ) -> Any:
        statement = (
            select(CatalogArticle, Supplier.name)
            .join(Supplier, CatalogArticle.supplier_id == Supplier.id)
            .where(func.jsonb_array_length(CatalogArticle.manual_overrides) > 0)
        )
        if supplier:
            statement = statement.where(Supplier.name.ilike(f"%{supplier.strip()}%"))
        if article_number:
            statement = statement.where(CatalogArticle.article_number.ilike(f"%{article_number.strip()}%"))
        return statement

    async def list_manual_overrides(
        self,
        *,
        supplier: str | None = None,
        article_number: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CatalogOverrideView], int]:
        statement = self._override_statement(
            supplier=supplier,
            article_number=article_number,
        )
        rows = (
            await self.session.execute(
                statement.order_by(Supplier.name, CatalogArticle.article_number)
                .offset(offset)
                .limit(max(1, limit))
            )
        ).all()
        count_statement = self._override_statement(
            supplier=supplier,
            article_number=article_number,
        ).with_only_columns(func.count())
        total = int((await self.session.execute(count_statement)).scalar_one())
        return [_override_view(article, name) for article, name in rows], total

    async def get_by_supplier_article(
        self,
        supplier_name: str,
        article_number: str,
    ) -> CatalogOverrideView | None:
        row = (
            await self.session.execute(
                select(CatalogArticle, Supplier.name)
                .join(Supplier, CatalogArticle.supplier_id == Supplier.id)
                .where(
                    Supplier.name == supplier_name.strip(),
                    CatalogArticle.article_number == article_number.strip(),
                )
            )
        ).first()
        if row is None:
            return None
        article, supplier = row
        return _override_view(article, supplier)
