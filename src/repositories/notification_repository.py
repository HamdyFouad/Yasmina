"""Live operational alerts for the in-app notification center."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import InvoiceStatus, SupplierInvoiceStatus
from src.models.orm import Invoice as InvoiceORM
from src.models.orm import QuotationSyncJob as QuotationSyncJobORM
from src.models.orm import SupplierInvoice as SupplierInvoiceORM
from src.models.orm import TenderIngestionJob as TenderIngestionJobORM

__all__ = ["NotificationAlert", "NotificationRepository"]


@dataclass(frozen=True, slots=True)
class NotificationAlert:
    id: str
    severity: str
    title: str
    message: str
    count: int
    href: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "count": self.count,
            "href": self.href,
        }


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def notifications(self) -> dict[str, Any]:
        procurement = (
            select(func.count())
            .select_from(SupplierInvoiceORM)
            .where(SupplierInvoiceORM.status == SupplierInvoiceStatus.EXCEPTION)
            .scalar_subquery()
        )
        quarantine = (
            select(func.count())
            .select_from(TenderIngestionJobORM)
            .where(TenderIngestionJobORM.quarantined.is_(True))
            .scalar_subquery()
        )
        dead_letter = (
            select(func.count())
            .select_from(TenderIngestionJobORM)
            .where(
                TenderIngestionJobORM.status == "failed",
                TenderIngestionJobORM.quarantined.is_(False),
            )
            .scalar_subquery()
        )
        outbox = (
            select(func.count())
            .select_from(QuotationSyncJobORM)
            .where(QuotationSyncJobORM.status == "failed")
            .scalar_subquery()
        )
        overdue = (
            select(func.count())
            .select_from(InvoiceORM)
            .where(InvoiceORM.status == InvoiceStatus.OVERDUE)
            .scalar_subquery()
        )
        procurement_count, quarantine_count, dead_count, outbox_count, overdue_count = (
            await self.session.execute(select(procurement, quarantine, dead_letter, outbox, overdue))
        ).one()

        alerts: list[NotificationAlert] = []
        if procurement_count:
            alerts.append(
                NotificationAlert(
                    id="procurement-exceptions",
                    severity="critical",
                    title="Procurement exceptions",
                    message=f"{int(procurement_count)} supplier invoice(s) need review.",
                    count=int(procurement_count),
                    href="/procurement",
                )
            )
        if quarantine_count:
            alerts.append(
                NotificationAlert(
                    id="clamav-quarantine",
                    severity="critical",
                    title="ClamAV quarantines",
                    message=f"{int(quarantine_count)} quarantined ingestion job(s) need review.",
                    count=int(quarantine_count),
                    href="/ops/workers",
                )
            )
        if dead_count:
            alerts.append(
                NotificationAlert(
                    id="dead-letter",
                    severity="critical" if int(dead_count) >= 5 else "warning",
                    title="Dead-letter queue",
                    message=f"{int(dead_count)} ingestion job(s) exhausted their retry budget.",
                    count=int(dead_count),
                    href="/ops/workers",
                )
            )
        if outbox_count:
            alerts.append(
                NotificationAlert(
                    id="outbox-failures",
                    severity="warning",
                    title="ERPNext outbox failures",
                    message=f"{int(outbox_count)} quotation sync job(s) failed.",
                    count=int(outbox_count),
                    href="/ops/outbox",
                )
            )
        if overdue_count:
            alerts.append(
                NotificationAlert(
                    id="overdue-invoices",
                    severity="warning",
                    title="Overdue invoices",
                    message=f"{int(overdue_count)} invoice(s) are past due.",
                    count=int(overdue_count),
                    href="/invoices",
                )
            )
        return {
            "alerts": [alert.as_dict() for alert in alerts],
            "count": len(alerts),
        }
