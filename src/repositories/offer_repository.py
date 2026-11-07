"""PostgreSQL persistence for customer offers and their line items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.enums import CustomerOfferStatus
from src.models.orm import CustomerOffer as CustomerOfferORM
from src.models.orm import CustomerOfferLine as CustomerOfferLineORM
from src.models.schemas import CustomerOffer, CustomerOfferLine

__all__ = [
    "OfferRepository",
    "OfferRepositoryConflict",
    "OfferRepositoryError",
    "StoredOffer",
    "offer_to_orm",
    "orm_to_offer",
]


class OfferRepositoryError(ValueError):
    """Raised when an offer cannot be persisted or loaded."""


class OfferRepositoryConflict(OfferRepositoryError):
    """Raised when an offer number is reused with a different approval payload."""


@dataclass(frozen=True, slots=True)
class StoredOffer:
    offer: CustomerOffer
    created: bool


def _line_to_orm(line: CustomerOfferLine, offer_id: UUID) -> CustomerOfferLineORM:
    return CustomerOfferLineORM(
        id=line.id,
        offer_id=offer_id,
        position_number=line.position_number,
        description=line.description,
        tender_item_id=line.tender_item_id,
        supplier_quote_line_id=line.supplier_quote_line_id,
        quantity=line.quantity,
        unit=line.unit,
        cost_unit_price_net=line.cost_unit_price_net,
        margin_rate=line.margin_rate,
        unit_price_net=line.unit_price_net,
        total_net=line.total_net,
        vat_rate=line.vat_rate,
    )


def _line_to_schema(line: CustomerOfferLineORM) -> CustomerOfferLine:
    return CustomerOfferLine(
        id=line.id,
        offer_id=line.offer_id,
        position_number=line.position_number,
        description=line.description,
        tender_item_id=line.tender_item_id,
        supplier_quote_line_id=line.supplier_quote_line_id,
        quantity=line.quantity,
        unit=line.unit,
        cost_unit_price_net=line.cost_unit_price_net,
        margin_rate=line.margin_rate,
        unit_price_net=line.unit_price_net,
        total_net=line.total_net,
        vat_rate=line.vat_rate,
    )


def offer_to_orm(offer: CustomerOffer) -> CustomerOfferORM:
    """Map a strict schema offer to its SQLAlchemy row graph."""

    orm = CustomerOfferORM(
        id=offer.id,
        offer_number=offer.offer_number,
        customer_name=offer.customer_name,
        customer_contact_email=(str(offer.customer_contact_email) if offer.customer_contact_email else None),
        customer_reference=offer.customer_reference,
        tender_id=offer.tender_id,
        status=offer.status,
        currency=offer.currency,
        margin_rate=offer.margin_rate,
        total_net=offer.total_net,
        vat_rate=offer.vat_rate,
        total_vat=offer.total_vat,
        total_gross=offer.total_gross,
        valid_until=offer.valid_until,
        payment_terms=offer.payment_terms,
        delivery_terms=offer.delivery_terms,
        ed25519_approval_signature=offer.ed25519_approval_signature,
        approval_payload_hash=offer.approval_payload_hash,
        approved_by=offer.approved_by,
        approved_at=offer.approved_at,
        owner=offer.owner,
        erpnext_quotation_name=offer.erpnext_quotation_name,
        invoice_artifacts=dict(offer.invoice_artifacts),
        gaeb_export_path=offer.gaeb_export_path,
        notes=offer.notes,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
    )
    orm.lines = [_line_to_orm(line, offer.id) for line in offer.lines]
    return orm


def orm_to_offer(orm: CustomerOfferORM) -> CustomerOffer:
    """Map a loaded SQLAlchemy offer graph back to the strict schema."""

    return CustomerOffer(
        id=orm.id,
        offer_number=orm.offer_number,
        customer_name=orm.customer_name,
        customer_contact_email=orm.customer_contact_email,
        customer_reference=orm.customer_reference,
        tender_id=orm.tender_id,
        status=orm.status,
        currency=orm.currency,
        lines=[_line_to_schema(line) for line in orm.lines],
        margin_rate=orm.margin_rate,
        total_net=orm.total_net,
        vat_rate=orm.vat_rate,
        total_vat=orm.total_vat,
        total_gross=orm.total_gross,
        valid_until=orm.valid_until,
        payment_terms=orm.payment_terms,
        delivery_terms=orm.delivery_terms,
        ed25519_approval_signature=orm.ed25519_approval_signature,
        approval_payload_hash=orm.approval_payload_hash,
        approved_by=orm.approved_by,
        approved_at=orm.approved_at,
        owner=orm.owner,
        erpnext_quotation_name=orm.erpnext_quotation_name,
        invoice_artifacts=dict(orm.invoice_artifacts or {}),
        gaeb_export_path=orm.gaeb_export_path,
        notes=orm.notes,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


class OfferRepository:
    """Persist offers idempotently by ``offer_number``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _statement(self) -> Select[Any]:
        return select(CustomerOfferORM).options(selectinload(CustomerOfferORM.lines))

    async def get(self, offer_id: UUID) -> StoredOffer | None:
        orm = (
            (await self.session.execute(self._statement().where(CustomerOfferORM.id == offer_id)))
            .scalars()
            .first()
        )
        return StoredOffer(offer=orm_to_offer(orm), created=False) if orm is not None else None

    async def get_by_number(self, offer_number: str) -> StoredOffer | None:
        orm = (
            (
                await self.session.execute(
                    self._statement().where(CustomerOfferORM.offer_number == offer_number)
                )
            )
            .scalars()
            .first()
        )
        return StoredOffer(offer=orm_to_offer(orm), created=False) if orm is not None else None

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        owner: str | None = None,
        status: CustomerOfferStatus | None = None,
        tender_id: UUID | None = None,
    ) -> list[CustomerOffer]:
        statement = self._statement().order_by(CustomerOfferORM.created_at.desc())
        if owner is not None:
            statement = statement.where(CustomerOfferORM.owner == owner)
        if status is not None:
            statement = statement.where(CustomerOfferORM.status == status)
        if tender_id is not None:
            statement = statement.where(CustomerOfferORM.tender_id == tender_id)
        statement = statement.offset(offset).limit(limit)
        rows = (await self.session.execute(statement)).scalars().all()
        return [orm_to_offer(row) for row in rows]

    async def count(
        self,
        *,
        owner: str | None = None,
        status: CustomerOfferStatus | None = None,
        tender_id: UUID | None = None,
    ) -> int:
        statement = select(func.count()).select_from(CustomerOfferORM)
        if owner is not None:
            statement = statement.where(CustomerOfferORM.owner == owner)
        if status is not None:
            statement = statement.where(CustomerOfferORM.status == status)
        if tender_id is not None:
            statement = statement.where(CustomerOfferORM.tender_id == tender_id)
        return int((await self.session.execute(statement)).scalar_one())

    async def save(self, offer: CustomerOffer, *, commit: bool = True) -> StoredOffer:
        """Insert or update an offer by number, preserving its identity.

        Reusing an offer number with a different approval signature/hash is a
        conflict. The same approved offer (an idempotent retry) updates only its
        artifacts/ERP metadata and returns the existing row.
        """

        existing_orm = (
            (
                await self.session.execute(
                    self._statement().where(CustomerOfferORM.offer_number == offer.offer_number)
                )
            )
            .scalars()
            .first()
        )

        if existing_orm is None:
            orm = offer_to_orm(offer)
            self.session.add(orm)
            try:
                if commit:
                    await self.session.commit()
                else:
                    await self.session.flush()
            except IntegrityError as exc:
                await self.session.rollback()
                raise OfferRepositoryConflict(
                    f"offer {offer.offer_number!r} already exists or violates a constraint"
                ) from exc
            return StoredOffer(offer=offer, created=True)

        existing = orm_to_offer(existing_orm)
        approved_states = {
            CustomerOfferStatus.APPROVED,
            CustomerOfferStatus.SENT,
            CustomerOfferStatus.ACCEPTED,
        }
        if existing.status in approved_states and offer.status not in approved_states:
            raise OfferRepositoryConflict(
                f"offer {offer.offer_number!r} is already {existing.status.value} and cannot be downgraded"
            )
        if (
            existing.ed25519_approval_signature is not None
            and offer.ed25519_approval_signature is not None
            and existing.ed25519_approval_signature != offer.ed25519_approval_signature
        ):
            raise OfferRepositoryConflict(
                f"offer {offer.offer_number!r} already has a different approval signature"
            )
        if (
            existing.approval_payload_hash is not None
            and offer.approval_payload_hash is not None
            and existing.approval_payload_hash != offer.approval_payload_hash
        ):
            raise OfferRepositoryConflict(
                f"offer {offer.offer_number!r} already has a different approval payload"
            )

        existing_orm.customer_name = offer.customer_name
        existing_orm.customer_contact_email = (
            str(offer.customer_contact_email) if offer.customer_contact_email else None
        )
        existing_orm.customer_reference = offer.customer_reference
        existing_orm.tender_id = offer.tender_id
        existing_orm.status = offer.status
        existing_orm.currency = offer.currency
        existing_orm.margin_rate = offer.margin_rate
        existing_orm.total_net = offer.total_net
        existing_orm.vat_rate = offer.vat_rate
        existing_orm.total_vat = offer.total_vat
        existing_orm.total_gross = offer.total_gross
        existing_orm.valid_until = offer.valid_until
        existing_orm.payment_terms = offer.payment_terms
        existing_orm.delivery_terms = offer.delivery_terms
        existing_orm.ed25519_approval_signature = offer.ed25519_approval_signature
        existing_orm.approval_payload_hash = offer.approval_payload_hash
        existing_orm.approved_by = offer.approved_by
        existing_orm.approved_at = offer.approved_at
        existing_orm.owner = offer.owner or existing_orm.owner
        existing_orm.erpnext_quotation_name = (
            offer.erpnext_quotation_name or existing_orm.erpnext_quotation_name
        )
        existing_orm.invoice_artifacts = {
            **dict(existing_orm.invoice_artifacts or {}),
            **dict(offer.invoice_artifacts),
        }
        existing_orm.gaeb_export_path = offer.gaeb_export_path or existing_orm.gaeb_export_path
        existing_orm.notes = offer.notes
        existing_orm.updated_at = datetime.now(UTC)

        existing_orm.lines.clear()
        await self.session.flush()
        existing_orm.lines.extend(_line_to_orm(line, existing_orm.id) for line in offer.lines)
        try:
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise OfferRepositoryError(f"offer {offer.offer_number!r} could not be updated: {exc}") from exc

        refreshed = await self.get_by_number(offer.offer_number)
        if refreshed is None:
            raise OfferRepositoryError(f"offer {offer.offer_number!r} disappeared after save")
        return StoredOffer(offer=refreshed.offer, created=False)
