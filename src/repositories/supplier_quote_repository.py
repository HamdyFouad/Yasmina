"""PostgreSQL persistence for supplier quotations received through n8n."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.enums import SupplierQuoteStatus
from src.models.orm import PurchaseOrder as PurchaseOrderORM
from src.models.orm import Supplier as SupplierORM
from src.models.orm import SupplierQuote as SupplierQuoteORM
from src.models.orm import SupplierQuoteLine as SupplierQuoteLineORM
from src.models.orm import SupplierQuoteTier as SupplierQuoteTierORM
from src.models.orm import Tender as TenderORM
from src.models.orm import TenderItem as TenderItemORM
from src.models.schemas import PriceTier, SupplierQuote, SupplierQuoteLine

__all__ = [
    "StoredSupplierQuote",
    "SupplierQuoteRepository",
    "SupplierQuoteRepositoryError",
    "quote_to_schema",
]


class SupplierQuoteRepositoryError(ValueError):
    """Raised when a callback quote cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class StoredSupplierQuote:
    quote_id: UUID
    created: bool


def quote_to_schema(orm: SupplierQuoteORM) -> SupplierQuote:
    """Map a loaded quote graph back to the strict domain schema."""

    return SupplierQuote(
        id=orm.id,
        supplier_id=orm.supplier_id,
        supplier_name=orm.supplier_name,
        supplier_contact_email=orm.supplier_contact_email,
        quote_reference=orm.quote_reference,
        tender_id=orm.tender_id,
        status=orm.status,
        currency=orm.currency,
        valid_until=orm.valid_until,
        payment_terms=orm.payment_terms,
        incoterms=orm.incoterms,
        delivery_days=orm.delivery_days,
        lines=[
            SupplierQuoteLine(
                id=line.id,
                supplier_quote_id=line.supplier_quote_id,
                line_number=line.line_number,
                description=line.description,
                tender_item_id=line.tender_item_id,
                material=line.material,
                color=line.color,
                glove_size=line.glove_size,
                quantity=line.quantity,
                unit=line.unit,
                unit_price_net=line.unit_price_net,
                moq=line.moq,
                tiers=[
                    PriceTier(min_quantity=tier.min_quantity, unit_price_net=tier.unit_price_net)
                    for tier in line.tiers
                ],
                pack_size=line.pack_size,
                gtin=line.gtin,
                delivery_days=line.delivery_days,
                notes=line.notes,
                attributes=dict(line.attributes or {}),
                is_accepted=line.is_accepted,
                accepted_at=line.accepted_at,
            )
            for line in orm.lines
        ],
        total_net=orm.total_net,
        accepted_at=orm.accepted_at,
        accepted_by=orm.accepted_by,
        source_document=orm.source_document,
        raw_extraction=dict(orm.raw_extraction or {}),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


class SupplierQuoteRepository:
    """Upsert-by-reference persistence for inbound supplier quotes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _supplier(self, quote: SupplierQuote) -> SupplierORM:
        if quote.supplier_id is not None:
            supplier = (
                await self.session.execute(select(SupplierORM).where(SupplierORM.id == quote.supplier_id))
            ).scalar_one_or_none()
            if supplier is None:
                raise SupplierQuoteRepositoryError(f"supplier {quote.supplier_id} does not exist")
            return supplier

        supplier = (
            await self.session.execute(select(SupplierORM).where(SupplierORM.name == quote.supplier_name))
        ).scalar_one_or_none()
        if supplier is not None:
            return supplier

        supplier = SupplierORM(
            name=quote.supplier_name,
            email=str(quote.supplier_contact_email) if quote.supplier_contact_email else None,
        )
        self.session.add(supplier)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise SupplierQuoteRepositoryError("supplier could not be created") from exc
        return supplier

    async def _validate_references(self, quote: SupplierQuote) -> None:
        if quote.tender_id is not None:
            tender = (
                await self.session.execute(select(TenderORM.id).where(TenderORM.id == quote.tender_id))
            ).scalar_one_or_none()
            if tender is None:
                raise SupplierQuoteRepositoryError(f"tender {quote.tender_id} does not exist")

        item_ids = [line.tender_item_id for line in quote.lines if line.tender_item_id is not None]
        if item_ids:
            found = set(
                (await self.session.execute(select(TenderItemORM.id).where(TenderItemORM.id.in_(item_ids))))
                .scalars()
                .all()
            )
            missing = [str(item_id) for item_id in item_ids if item_id not in found]
            if missing:
                raise SupplierQuoteRepositoryError("unknown tender item(s): " + ", ".join(sorted(missing)))

    def _orm(self, quote: SupplierQuote, supplier: SupplierORM) -> SupplierQuoteORM:
        orm = SupplierQuoteORM(
            id=quote.id,
            supplier_id=supplier.id,
            supplier_name=quote.supplier_name,
            supplier_contact_email=(
                str(quote.supplier_contact_email) if quote.supplier_contact_email else None
            ),
            quote_reference=quote.quote_reference,
            tender_id=quote.tender_id,
            status=quote.status,
            currency=quote.currency,
            valid_until=quote.valid_until,
            payment_terms=quote.payment_terms,
            incoterms=quote.incoterms,
            delivery_days=quote.delivery_days,
            accepted_at=quote.accepted_at,
            accepted_by=quote.accepted_by,
            total_net=quote.total_net,
            source_document=quote.source_document,
            raw_extraction=dict(quote.raw_extraction),
        )
        for line in quote.lines:
            line_orm = SupplierQuoteLineORM(
                id=line.id,
                supplier_quote_id=quote.id,
                line_number=line.line_number,
                description=line.description,
                tender_item_id=line.tender_item_id,
                material=line.material,
                color=line.color,
                glove_size=line.glove_size,
                quantity=line.quantity,
                unit=line.unit,
                unit_price_net=line.unit_price_net,
                moq=line.moq,
                pack_size=line.pack_size,
                gtin=line.gtin,
                delivery_days=line.delivery_days,
                notes=line.notes,
                attributes=dict(line.attributes),
                is_accepted=line.is_accepted,
                accepted_at=line.accepted_at,
                tiers=[
                    SupplierQuoteTierORM(
                        min_quantity=tier.min_quantity,
                        unit_price_net=tier.unit_price_net,
                    )
                    for tier in line.tiers
                ],
            )
            orm.lines.append(line_orm)
        return orm

    async def get(self, quote_id: UUID) -> SupplierQuote | None:
        statement = (
            select(SupplierQuoteORM)
            .where(SupplierQuoteORM.id == quote_id)
            .options(selectinload(SupplierQuoteORM.lines).selectinload(SupplierQuoteLineORM.tiers))
        )
        orm = (await self.session.execute(statement)).scalars().first()
        return quote_to_schema(orm) if orm is not None else None

    async def list_for_tender(
        self,
        tender_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SupplierQuote]:
        statement = (
            select(SupplierQuoteORM)
            .where(SupplierQuoteORM.tender_id == tender_id)
            .options(selectinload(SupplierQuoteORM.lines).selectinload(SupplierQuoteLineORM.tiers))
            .order_by(SupplierQuoteORM.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [quote_to_schema(row) for row in rows]

    async def count_for_tender(self, tender_id: UUID) -> int:
        statement = (
            select(func.count()).select_from(SupplierQuoteORM).where(SupplierQuoteORM.tender_id == tender_id)
        )
        return int((await self.session.execute(statement)).scalar_one())

    async def list_accepted_without_order(self) -> list[SupplierQuote]:
        statement = (
            select(SupplierQuoteORM)
            .outerjoin(
                PurchaseOrderORM,
                PurchaseOrderORM.supplier_quote_id == SupplierQuoteORM.id,
            )
            .where(
                SupplierQuoteORM.status == SupplierQuoteStatus.ACCEPTED,
                SupplierQuoteORM.supplier_id.is_not(None),
                PurchaseOrderORM.id.is_(None),
            )
            .options(selectinload(SupplierQuoteORM.lines).selectinload(SupplierQuoteLineORM.tiers))
            .order_by(SupplierQuoteORM.accepted_at.desc(), SupplierQuoteORM.created_at.desc())
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [quote_to_schema(row) for row in rows]

    async def accept(self, quote_id: UUID, *, actor: str) -> SupplierQuote:
        row = (
            await self.session.execute(
                select(SupplierQuoteORM)
                .where(SupplierQuoteORM.id == quote_id)
                .options(selectinload(SupplierQuoteORM.lines))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise SupplierQuoteRepositoryError("supplier quote not found")
        if row.status in {SupplierQuoteStatus.REJECTED, SupplierQuoteStatus.EXPIRED}:
            raise SupplierQuoteRepositoryError(f"supplier quote cannot be accepted from {row.status.value}")
        if row.status == SupplierQuoteStatus.ACCEPTED:
            loaded = await self.get(quote_id)
            if loaded is None:
                raise SupplierQuoteRepositoryError("supplier quote not found")
            return loaded
        now = datetime.now(UTC)
        row.status = SupplierQuoteStatus.ACCEPTED
        row.accepted_at = now
        row.accepted_by = actor
        for line in row.lines:
            line.is_accepted = True
            line.accepted_at = now
        await self.session.commit()
        loaded = await self.get(quote_id)
        if loaded is None:
            raise SupplierQuoteRepositoryError("supplier quote disappeared after acceptance")
        return loaded

    async def save(self, quote: SupplierQuote) -> StoredSupplierQuote:
        """Persist a quote, returning the existing row for a repeated reference."""

        await self._validate_references(quote)
        supplier = await self._supplier(quote)

        existing = (
            await self.session.execute(
                select(SupplierQuoteORM).where(
                    SupplierQuoteORM.supplier_id == supplier.id,
                    SupplierQuoteORM.quote_reference == quote.quote_reference,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return StoredSupplierQuote(quote_id=existing.id, created=False)

        self.session.add(self._orm(quote, supplier))
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise SupplierQuoteRepositoryError(
                f"supplier quote {quote.quote_reference!r} conflicts with existing data"
            ) from exc
        return StoredSupplierQuote(quote_id=quote.id, created=True)
