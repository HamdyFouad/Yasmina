"""PostgreSQL-backed tender repository."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm import Tender as TenderORM
from src.models.orm import TenderItem as TenderItemORM
from src.models.schemas import Tender, TenderItem
from src.parsers.gaeb_parser import parse_gaeb

__all__ = ["StoredTender", "TenderRepository", "TenderRepositoryError"]


class TenderRepositoryError(ValueError):
    """Raised when a tender cannot be persisted or loaded."""


@dataclass(frozen=True, slots=True)
class StoredTender:
    tender: Tender
    document: Any = None
    source_filename: str | None = None
    source_path: Path | None = None
    source_object: str | None = None


def _item_to_orm(item: TenderItem, tender_id: UUID) -> TenderItemORM:
    return TenderItemORM(
        id=item.id,
        tender_id=tender_id,
        position_number=item.position_number,
        lot=item.lot,
        short_text=item.short_text,
        long_text=item.long_text,
        quantity=item.quantity,
        unit=item.unit,
        glove_size=item.glove_size,
        material=item.material,
        color=item.color,
        thickness_um=item.thickness_um,
        dimensions=item.dimensions,
        pack_size=item.pack_size,
        packs_per_carton=item.packs_per_carton,
        packaging_description=item.packaging_description,
        delivery_location=item.delivery_location,
        requested_delivery_date=item.requested_delivery_date,
        cpv_code=item.cpv_code,
        is_alternative_allowed=item.is_alternative_allowed,
        notes=item.notes,
        source_reference=item.source_reference,
        attributes=dict(item.attributes),
    )


def _item_to_schema(item: TenderItemORM) -> TenderItem:
    return TenderItem(
        id=item.id,
        tender_id=item.tender_id,
        position_number=item.position_number,
        lot=item.lot,
        short_text=item.short_text,
        long_text=item.long_text,
        quantity=item.quantity,
        unit=item.unit,
        glove_size=item.glove_size,
        material=item.material,
        color=item.color,
        thickness_um=item.thickness_um,
        dimensions=item.dimensions,
        pack_size=item.pack_size,
        packs_per_carton=item.packs_per_carton,
        packaging_description=item.packaging_description,
        delivery_location=item.delivery_location,
        requested_delivery_date=item.requested_delivery_date,
        cpv_code=item.cpv_code,
        is_alternative_allowed=item.is_alternative_allowed,
        notes=item.notes,
        source_reference=item.source_reference,
        attributes=dict(item.attributes or {}),
    )


def tender_to_orm(tender: Tender) -> TenderORM:
    """Map a strict schema tender to its SQLAlchemy row graph."""

    orm = TenderORM(
        id=tender.id,
        tender_number=tender.tender_number,
        title=tender.title,
        description=tender.description,
        client_name=tender.client_name,
        client_contact_email=str(tender.client_contact_email) if tender.client_contact_email else None,
        procedure=tender.procedure,
        source_format=tender.source_format,
        source_reference=tender.source_reference,
        cpv_codes=list(tender.cpv_codes),
        publication_date=tender.publication_date,
        submission_deadline=tender.submission_deadline,
        currency=tender.currency,
        status=tender.status,
        vat_rate=tender.vat_rate,
        metadata_json=dict(tender.metadata),
        created_at=tender.created_at,
        updated_at=tender.updated_at,
    )
    orm.items = [_item_to_orm(item, tender.id) for item in tender.items]
    return orm


def orm_to_tender(orm: TenderORM) -> Tender:
    """Map a loaded SQLAlchemy tender graph back to the strict schema."""

    return Tender(
        id=orm.id,
        tender_number=orm.tender_number,
        title=orm.title,
        description=orm.description,
        client_name=orm.client_name,
        client_contact_email=orm.client_contact_email,
        procedure=orm.procedure,
        source_format=orm.source_format,
        source_reference=orm.source_reference,
        cpv_codes=list(orm.cpv_codes or []),
        publication_date=orm.publication_date,
        submission_deadline=orm.submission_deadline,
        currency=orm.currency,
        status=orm.status,
        vat_rate=orm.vat_rate,
        items=[_item_to_schema(item) for item in orm.items],
        metadata=dict(orm.metadata_json or {}),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


class TenderRepository:
    """Persistence boundary for tender headers and their positions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(
        self,
        tender: Tender,
        *,
        source_filename: str | None = None,
        source_path: str | Path | None = None,
        source_object: str | None = None,
        document: Any = None,
    ) -> StoredTender:
        metadata = dict(tender.metadata)
        if source_filename:
            metadata["_source_filename"] = source_filename
        if source_path:
            metadata["_source_path"] = str(source_path)
        if source_object:
            metadata["_source_object"] = source_object
        stored_tender = tender.model_copy(update={"metadata": metadata})
        orm = tender_to_orm(stored_tender)
        self.session.add(orm)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise TenderRepositoryError(f"tender could not be stored: {exc}") from exc
        public_metadata = {key: value for key, value in metadata.items() if not key.startswith("_")}
        return StoredTender(
            tender=tender.model_copy(update={"metadata": public_metadata}),
            document=document,
            source_filename=source_filename,
            source_path=Path(source_path) if source_path else None,
            source_object=source_object,
        )

    async def get(self, tender_id: UUID) -> StoredTender | None:
        statement = (
            select(TenderORM)
            .where(TenderORM.id == tender_id)
            .options(selectinload(TenderORM.items))
        )
        orm = (await self.session.execute(statement)).scalars().first()
        if orm is None:
            return None

        tender = orm_to_tender(orm)
        metadata = dict(tender.metadata or {})
        source_path = metadata.get("_source_path")
        source_filename = metadata.get("_source_filename")
        source_object = metadata.get("_source_object")
        public_metadata = {key: value for key, value in metadata.items() if not key.startswith("_")}
        public_tender = tender.model_copy(update={"metadata": public_metadata})
        document = None
        path: Path | None = Path(source_path) if source_path else None
        if path is not None and path.suffix.lower() in {".x83", ".d83"} and path.is_file():
            try:
                document = parse_gaeb(path).document
            except Exception:
                document = None
        return StoredTender(
            tender=public_tender,
            document=document,
            source_filename=source_filename,
            source_path=path,
            source_object=source_object,
        )

    async def delete(self, tender_id: UUID) -> bool:
        orm = await self.session.get(TenderORM, tender_id)
        if orm is None:
            return False
        await self.session.delete(orm)
        await self.session.commit()
        return True
