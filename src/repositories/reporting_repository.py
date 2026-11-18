"""Efficient aggregate queries for the reporting workspace."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import CustomerOfferStatus, InvoiceStatus
from src.models.orm import CustomerOffer as CustomerOfferORM
from src.models.orm import CustomerOfferLine as CustomerOfferLineORM
from src.models.orm import Invoice as InvoiceORM
from src.models.orm import Supplier as SupplierORM
from src.models.orm import SupplierQuote as SupplierQuoteORM
from src.models.orm import SupplierQuoteLine as SupplierQuoteLineORM
from src.models.orm import Tender as TenderORM
from src.models.schemas import q2, q4

__all__ = [
    "AgingBucket",
    "AgingReport",
    "ConversionMetrics",
    "MarginBreakdown",
    "PipelineStage",
    "ReportingRepository",
    "ReportingSummary",
    "WinLoss",
]


@dataclass(frozen=True, slots=True)
class PipelineStage:
    status: str
    count: int
    total_net: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "count": self.count,
            "total_net": str(self.total_net),
        }


@dataclass(frozen=True, slots=True)
class WinLoss:
    won_count: int
    lost_count: int
    won_value: Decimal
    lost_value: Decimal
    win_rate: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "won_count": self.won_count,
            "lost_count": self.lost_count,
            "won_value": str(self.won_value),
            "lost_value": str(self.lost_value),
            "win_rate": str(self.win_rate) if self.win_rate is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ConversionMetrics:
    total_tenders: int
    tenders_with_offers: int
    tenders_with_accepted_offers: int
    offer_conversion_rate: Decimal | None
    tender_coverage_rate: Decimal | None
    tender_win_rate: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_tenders": self.total_tenders,
            "tenders_with_offers": self.tenders_with_offers,
            "tenders_with_accepted_offers": self.tenders_with_accepted_offers,
            "offer_conversion_rate": (
                str(self.offer_conversion_rate) if self.offer_conversion_rate is not None else None
            ),
            "tender_coverage_rate": (
                str(self.tender_coverage_rate) if self.tender_coverage_rate is not None else None
            ),
            "tender_win_rate": (str(self.tender_win_rate) if self.tender_win_rate is not None else None),
        }


@dataclass(frozen=True, slots=True)
class ReportingSummary:
    tenders: int
    offers: int
    pipeline_value: Decimal
    won_offers: int
    win_rate: Decimal | None
    gross_invoiced: Decimal
    outstanding: Decimal
    overdue_amount: Decimal
    currency: str
    pipeline: tuple[PipelineStage, ...]
    win_loss: WinLoss
    conversion: ConversionMetrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "kpis": {
                "tenders": self.tenders,
                "offers": self.offers,
                "pipeline_value": str(self.pipeline_value),
                "won_offers": self.won_offers,
                "win_rate": str(self.win_rate) if self.win_rate is not None else None,
                "gross_invoiced": str(self.gross_invoiced),
                "outstanding": str(self.outstanding),
                "overdue_amount": str(self.overdue_amount),
                "currency": self.currency,
            },
            "pipeline": [stage.as_dict() for stage in self.pipeline],
            "win_loss": self.win_loss.as_dict(),
            "conversion": self.conversion.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class MarginBreakdown:
    dimension: str
    label: str
    offer_count: int
    revenue_net: Decimal
    cost_net: Decimal
    margin_net: Decimal
    margin_rate: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "label": self.label,
            "offer_count": self.offer_count,
            "revenue_net": str(self.revenue_net),
            "cost_net": str(self.cost_net),
            "margin_net": str(self.margin_net),
            "margin_rate": str(self.margin_rate) if self.margin_rate is not None else None,
        }


@dataclass(frozen=True, slots=True)
class AgingBucket:
    label: str
    invoice_count: int
    total_gross: Decimal
    paid_amount: Decimal
    outstanding: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "invoice_count": self.invoice_count,
            "total_gross": str(self.total_gross),
            "paid_amount": str(self.paid_amount),
            "outstanding": str(self.outstanding),
        }


@dataclass(frozen=True, slots=True)
class AgingReport:
    buckets: tuple[AgingBucket, ...]
    invoice_count: int
    paid_total: Decimal
    overdue_total: Decimal
    outstanding_total: Decimal
    currency: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "buckets": [bucket.as_dict() for bucket in self.buckets],
            "invoice_count": self.invoice_count,
            "paid_total": str(self.paid_total),
            "overdue_total": str(self.overdue_total),
            "outstanding_total": str(self.outstanding_total),
            "currency": self.currency,
        }


def _decimal(value: Any) -> Decimal:
    return q2(Decimal(str(value or 0)))


def _rate(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return q4(numerator / denominator) if denominator > 0 else None


class ReportingRepository:
    """Read-only aggregates, scoped to an owner for sales users."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _offer_filter(self, statement: Any, owner: str | None) -> Any:
        if owner is not None:
            statement = statement.where(CustomerOfferORM.owner == owner)
        return statement

    async def _pipeline(self, owner: str | None) -> tuple[PipelineStage, ...]:
        statement = (
            select(
                CustomerOfferORM.status,
                func.count(CustomerOfferORM.id),
                func.coalesce(func.sum(CustomerOfferORM.total_net), 0),
            )
            .select_from(CustomerOfferORM)
            .group_by(CustomerOfferORM.status)
        )
        statement = self._offer_filter(statement, owner)
        rows = (await self.session.execute(statement)).all()
        by_status = {
            (row[0].value if hasattr(row[0], "value") else str(row[0])): (
                int(row[1] or 0),
                _decimal(row[2]),
            )
            for row in rows
        }
        return tuple(
            PipelineStage(
                status=status.value,
                count=by_status.get(status.value, (0, Decimal("0.00")))[0],
                total_net=by_status.get(status.value, (0, Decimal("0.00")))[1],
            )
            for status in CustomerOfferStatus
        )

    async def _conversion(self, owner: str | None) -> ConversionMetrics:
        total_tenders_statement = select(func.count()).select_from(TenderORM)
        if owner is not None:
            total_tenders_statement = total_tenders_statement.where(TenderORM.owner == owner)
        total_tenders = int((await self.session.execute(total_tenders_statement)).scalar_one())

        offers_by_tender = (
            select(func.count(func.distinct(CustomerOfferORM.tender_id)))
            .select_from(CustomerOfferORM)
            .where(CustomerOfferORM.tender_id.is_not(None))
        )
        offers_by_tender = self._offer_filter(offers_by_tender, owner)
        tenders_with_offers = int((await self.session.execute(offers_by_tender)).scalar_one() or 0)

        accepted_by_tender = (
            select(func.count(func.distinct(CustomerOfferORM.tender_id)))
            .select_from(CustomerOfferORM)
            .where(
                CustomerOfferORM.tender_id.is_not(None),
                CustomerOfferORM.status == CustomerOfferStatus.ACCEPTED,
            )
        )
        accepted_by_tender = self._offer_filter(accepted_by_tender, owner)
        tenders_with_accepted = int((await self.session.execute(accepted_by_tender)).scalar_one() or 0)
        offer_count_statement = self._offer_filter(select(func.count()).select_from(CustomerOfferORM), owner)
        offer_count = int((await self.session.execute(offer_count_statement)).scalar_one() or 0)
        accepted_count_statement = self._offer_filter(
            select(func.count())
            .select_from(CustomerOfferORM)
            .where(CustomerOfferORM.status == CustomerOfferStatus.ACCEPTED),
            owner,
        )
        accepted_count = int((await self.session.execute(accepted_count_statement)).scalar_one() or 0)
        return ConversionMetrics(
            total_tenders=total_tenders,
            tenders_with_offers=tenders_with_offers,
            tenders_with_accepted_offers=tenders_with_accepted,
            offer_conversion_rate=_rate(Decimal(accepted_count), Decimal(offer_count)),
            tender_coverage_rate=_rate(Decimal(tenders_with_offers), Decimal(total_tenders)),
            tender_win_rate=_rate(Decimal(tenders_with_accepted), Decimal(tenders_with_offers)),
        )

    async def summary(self, *, owner: str | None = None) -> ReportingSummary:
        pipeline = await self._pipeline(owner)
        conversion = await self._conversion(owner)
        pipeline_value = q2(
            sum(
                (
                    stage.total_net
                    for stage in pipeline
                    if stage.status
                    in {
                        CustomerOfferStatus.DRAFT.value,
                        CustomerOfferStatus.PENDING_APPROVAL.value,
                        CustomerOfferStatus.APPROVED.value,
                        CustomerOfferStatus.SENT.value,
                    }
                ),
                start=Decimal("0.00"),
            )
        )
        won = next(
            (stage for stage in pipeline if stage.status == CustomerOfferStatus.ACCEPTED.value),
            PipelineStage(CustomerOfferStatus.ACCEPTED.value, 0, Decimal("0.00")),
        )
        lost_statuses = {
            CustomerOfferStatus.REJECTED.value,
            CustomerOfferStatus.EXPIRED.value,
        }
        lost_count = sum(stage.count for stage in pipeline if stage.status in lost_statuses)
        lost_value = q2(
            sum(
                (stage.total_net for stage in pipeline if stage.status in lost_statuses),
                start=Decimal("0.00"),
            )
        )
        win_loss = WinLoss(
            won_count=won.count,
            lost_count=lost_count,
            won_value=won.total_net,
            lost_value=lost_value,
            win_rate=_rate(Decimal(won.count), Decimal(won.count + lost_count)),
        )
        aging = await self.aging(owner=owner)
        return ReportingSummary(
            tenders=conversion.total_tenders,
            offers=sum(stage.count for stage in pipeline),
            pipeline_value=pipeline_value,
            won_offers=won.count,
            win_rate=win_loss.win_rate,
            gross_invoiced=aging.paid_total + aging.outstanding_total,
            outstanding=aging.outstanding_total,
            overdue_amount=aging.overdue_total,
            currency=aging.currency,
            pipeline=pipeline,
            win_loss=win_loss,
            conversion=conversion,
        )

    async def margins(self, *, owner: str | None = None) -> dict[str, list[MarginBreakdown]]:
        offer_margin_statement = (
            select(
                CustomerOfferORM.id.label("offer_id"),
                CustomerOfferORM.customer_name.label("customer_name"),
                CustomerOfferORM.tender_id.label("tender_id"),
                CustomerOfferORM.total_net.label("revenue_net"),
                func.sum(CustomerOfferLineORM.quantity * CustomerOfferLineORM.cost_unit_price_net).label(
                    "cost_net"
                ),
            )
            .select_from(CustomerOfferORM)
            .join(CustomerOfferLineORM, CustomerOfferLineORM.offer_id == CustomerOfferORM.id)
            .group_by(
                CustomerOfferORM.id,
                CustomerOfferORM.customer_name,
                CustomerOfferORM.tender_id,
                CustomerOfferORM.total_net,
            )
        )
        offer_margin = self._offer_filter(offer_margin_statement, owner).subquery()

        customer_statement = (
            select(
                offer_margin.c.customer_name,
                func.count(offer_margin.c.offer_id),
                func.coalesce(func.sum(offer_margin.c.revenue_net), 0),
                func.coalesce(func.sum(offer_margin.c.cost_net), 0),
            )
            .select_from(offer_margin)
            .group_by(offer_margin.c.customer_name)
            .order_by(func.sum(offer_margin.c.revenue_net).desc())
            .limit(25)
        )
        customer_rows = (await self.session.execute(customer_statement)).all()
        customers = [_margin("customer", str(row[0]), int(row[1]), row[2], row[3]) for row in customer_rows]

        tender_statement = (
            select(
                TenderORM.tender_number,
                TenderORM.title,
                func.count(offer_margin.c.offer_id),
                func.coalesce(func.sum(offer_margin.c.revenue_net), 0),
                func.coalesce(func.sum(offer_margin.c.cost_net), 0),
            )
            .select_from(offer_margin)
            .join(TenderORM, TenderORM.id == offer_margin.c.tender_id)
            .group_by(TenderORM.id, TenderORM.tender_number, TenderORM.title)
            .order_by(func.sum(offer_margin.c.revenue_net).desc())
            .limit(25)
        )
        tender_rows = (await self.session.execute(tender_statement)).all()
        tenders = [
            _margin(
                "tender",
                f"{row[0]} · {row[1]}",
                int(row[2]),
                row[3],
                row[4],
            )
            for row in tender_rows
        ]

        supplier_statement = (
            select(
                SupplierORM.name,
                func.count(func.distinct(CustomerOfferORM.id)),
                func.coalesce(func.sum(CustomerOfferLineORM.total_net), 0),
                func.coalesce(
                    func.sum(CustomerOfferLineORM.quantity * CustomerOfferLineORM.cost_unit_price_net),
                    0,
                ),
            )
            .select_from(CustomerOfferLineORM)
            .join(CustomerOfferORM, CustomerOfferORM.id == CustomerOfferLineORM.offer_id)
            .outerjoin(
                SupplierQuoteLineORM,
                SupplierQuoteLineORM.id == CustomerOfferLineORM.supplier_quote_line_id,
            )
            .outerjoin(
                SupplierQuoteORM,
                SupplierQuoteORM.id == SupplierQuoteLineORM.supplier_quote_id,
            )
            .outerjoin(SupplierORM, SupplierORM.id == SupplierQuoteORM.supplier_id)
            .group_by(SupplierORM.name)
            .order_by(func.sum(CustomerOfferLineORM.total_net).desc())
            .limit(25)
        )
        supplier_statement = self._offer_filter(supplier_statement, owner)
        supplier_rows = (await self.session.execute(supplier_statement)).all()
        suppliers = [
            _margin(
                "supplier",
                str(row[0]) if row[0] is not None else "Unassigned",
                int(row[1]),
                row[2],
                row[3],
            )
            for row in supplier_rows
        ]
        return {"customers": customers, "tenders": tenders, "suppliers": suppliers}

    async def aging(self, *, owner: str | None = None, today: date | None = None) -> AgingReport:
        current = today or date.today()
        bucket = case(
            (InvoiceORM.due_date >= current, "Current"),
            (InvoiceORM.due_date >= current - timedelta(days=30), "1–30 days"),
            (InvoiceORM.due_date >= current - timedelta(days=60), "31–60 days"),
            (InvoiceORM.due_date >= current - timedelta(days=90), "61–90 days"),
            else_="90+ days",
        )
        statement = (
            select(
                bucket.label("bucket"),
                func.count(InvoiceORM.id),
                func.coalesce(func.sum(InvoiceORM.total_gross), 0),
                func.coalesce(func.sum(InvoiceORM.paid_amount), 0),
                func.coalesce(
                    func.sum(InvoiceORM.total_gross - InvoiceORM.paid_amount - InvoiceORM.credited_amount),
                    0,
                ),
            )
            .select_from(InvoiceORM)
            .join(CustomerOfferORM, CustomerOfferORM.id == InvoiceORM.offer_id)
            .where(InvoiceORM.status != InvoiceStatus.CANCELLED)
            .group_by(bucket)
        )
        if owner is not None:
            statement = statement.where(CustomerOfferORM.owner == owner)
        rows = (await self.session.execute(statement)).all()
        by_label = {str(row[0]): (int(row[1]), q2(row[2]), q2(row[3]), q2(row[4])) for row in rows}
        buckets = tuple(
            AgingBucket(
                label=label,
                invoice_count=by_label.get(label, (0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")))[0],
                total_gross=by_label.get(label, (0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")))[1],
                paid_amount=by_label.get(label, (0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")))[2],
                outstanding=by_label.get(label, (0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")))[3],
            )
            for label in ("Current", "1–30 days", "31–60 days", "61–90 days", "90+ days")
        )
        paid_statement = (
            select(func.coalesce(func.sum(InvoiceORM.total_gross), 0))
            .select_from(InvoiceORM)
            .join(CustomerOfferORM, CustomerOfferORM.id == InvoiceORM.offer_id)
            .where(InvoiceORM.status == InvoiceStatus.PAID)
        )
        overdue_statement = (
            select(func.coalesce(func.sum(InvoiceORM.total_gross), 0))
            .select_from(InvoiceORM)
            .join(CustomerOfferORM, CustomerOfferORM.id == InvoiceORM.offer_id)
            .where(InvoiceORM.status == InvoiceStatus.OVERDUE)
        )
        if owner is not None:
            paid_statement = paid_statement.where(CustomerOfferORM.owner == owner)
            overdue_statement = overdue_statement.where(CustomerOfferORM.owner == owner)
        paid_total = _decimal((await self.session.execute(paid_statement)).scalar_one())
        overdue_total = _decimal((await self.session.execute(overdue_statement)).scalar_one())
        return AgingReport(
            buckets=buckets,
            invoice_count=sum(bucket.invoice_count for bucket in buckets),
            paid_total=paid_total,
            overdue_total=overdue_total,
            outstanding_total=q2(sum((bucket.outstanding for bucket in buckets), start=Decimal("0.00"))),
            currency="EUR",
        )


def _margin(
    dimension: str,
    label: str,
    offer_count: int,
    revenue: Any,
    cost: Any,
) -> MarginBreakdown:
    revenue_net = _decimal(revenue)
    cost_net = _decimal(cost)
    margin_net = q2(revenue_net - cost_net)
    return MarginBreakdown(
        dimension=dimension,
        label=label,
        offer_count=offer_count,
        revenue_net=revenue_net,
        cost_net=cost_net,
        margin_net=margin_net,
        margin_rate=_rate(margin_net, revenue_net),
    )
