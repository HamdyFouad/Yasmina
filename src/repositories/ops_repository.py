"""Dashboard aggregates over tenders, offers, invoices and the audit log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import CustomerOfferStatus, InvoiceStatus
from src.models.orm import AuditLog
from src.models.orm import CustomerOffer as CustomerOfferORM
from src.models.orm import Invoice as InvoiceORM
from src.models.orm import Tender as TenderORM

__all__ = ["ActivityEvent", "OpsRepository"]


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    id: int
    occurred_at: datetime
    event_type: str
    actor: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "occurred_at": self.occurred_at.isoformat(),
            "event_type": self.event_type,
            "actor": self.actor,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": dict(self.payload),
        }


def _money(value: Any) -> str:
    return format(Decimal(str(value or 0)), "f")


class OpsRepository:
    """Read-only aggregates; ``owner`` scopes the figures for sales users."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def recent_activity(self, *, limit: int = 50) -> list[ActivityEvent]:
        rows = (
            (
                await self.session.execute(
                    select(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(limit, 200)))
                )
            )
            .scalars()
            .all()
        )
        return [
            ActivityEvent(
                id=row.id,
                occurred_at=row.occurred_at,
                event_type=str(row.event_type),
                actor=row.actor,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                payload=dict(row.payload or {}),
            )
            for row in rows
        ]

    async def summary(self, *, owner: str | None = None) -> dict[str, Any]:
        tender_statement = select(func.count()).select_from(TenderORM)
        offer_statement = select(func.count()).select_from(CustomerOfferORM)
        pending_statement = (
            select(func.count())
            .select_from(CustomerOfferORM)
            .where(CustomerOfferORM.status == CustomerOfferStatus.PENDING_APPROVAL)
        )
        invoice_statement = (
            select(
                func.count(InvoiceORM.id),
                func.coalesce(func.sum(InvoiceORM.total_gross), 0),
                func.coalesce(func.sum(InvoiceORM.paid_amount), 0),
            )
            .select_from(InvoiceORM)
            .join(CustomerOfferORM, InvoiceORM.offer_id == CustomerOfferORM.id)
        )
        overdue_statement = (
            select(func.count(InvoiceORM.id))
            .select_from(InvoiceORM)
            .join(CustomerOfferORM, InvoiceORM.offer_id == CustomerOfferORM.id)
            .where(InvoiceORM.status == InvoiceStatus.OVERDUE)
        )
        if owner is not None:
            tender_statement = tender_statement.where(TenderORM.owner == owner)
            offer_statement = offer_statement.where(CustomerOfferORM.owner == owner)
            pending_statement = pending_statement.where(CustomerOfferORM.owner == owner)
            invoice_statement = invoice_statement.where(CustomerOfferORM.owner == owner)
            overdue_statement = overdue_statement.where(CustomerOfferORM.owner == owner)

        invoice_count, gross_invoiced, gross_paid = (await self.session.execute(invoice_statement)).one()
        return {
            "tenders": int((await self.session.execute(tender_statement)).scalar_one()),
            "offers": int((await self.session.execute(offer_statement)).scalar_one()),
            "pending_approvals": int((await self.session.execute(pending_statement)).scalar_one()),
            "invoices": int(invoice_count or 0),
            "overdue_invoices": int((await self.session.execute(overdue_statement)).scalar_one()),
            "gross_invoiced": _money(gross_invoiced),
            "gross_paid": _money(gross_paid),
            "currency": "EUR",
        }
