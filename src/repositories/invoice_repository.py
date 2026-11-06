"""Durable invoices, transactionally gapless numbering and VAT records."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.generators.invoice_models import Invoice, InvoiceLine, InvoiceParty
from src.models.enums import InvoiceStatus
from src.models.orm import (
    CustomerOffer as CustomerOfferORM,
)
from src.models.orm import (
    Invoice as InvoiceORM,
)
from src.models.orm import (
    InvoiceLine as InvoiceLineORM,
)
from src.models.orm import (
    InvoiceNumberSequence,
    InvoiceVatSplit,
)
from src.models.schemas import q2
from src.repositories.invoice_event_repository import InvoiceEventRepository

__all__ = [
    "InvoiceNumberExhaustedError",
    "InvoiceRepository",
    "InvoiceRepositoryConflict",
    "InvoiceRepositoryError",
    "StoredInvoice",
]

_MAX_SEQUENCE = 9999


class InvoiceRepositoryError(ValueError):
    """Raised when an invoice cannot be persisted."""


class InvoiceRepositoryConflict(InvoiceRepositoryError):
    """Raised when an offer already has an invoice or a number is duplicated."""


class InvoiceNumberExhaustedError(InvoiceRepositoryError):
    """Raised when the yearly INV-YYYY-XXXX space is exhausted."""


@dataclass(frozen=True, slots=True)
class StoredInvoice:
    id: UUID
    offer_id: UUID
    invoice: Invoice
    status: InvoiceStatus
    paid_amount: Decimal
    credited_amount: Decimal
    paid_at: datetime | None
    credit_note_number: str | None
    vat_breakdown: tuple[dict[str, str], ...]
    artifacts: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "offer_id": str(self.offer_id),
            "invoice_number": self.invoice.invoice_number,
            "issue_date": self.invoice.issue_date.isoformat(),
            "due_date": self.invoice.due_date.isoformat(),
            "status": self.status.value,
            "currency": self.invoice.currency.value,
            "total_net": format(self.invoice.total_net, "f"),
            "total_vat": format(self.invoice.total_vat, "f"),
            "total_gross": format(self.invoice.total_gross, "f"),
            "paid_amount": format(self.paid_amount, "f"),
            "credited_amount": format(self.credited_amount, "f"),
            "paid_at": self.paid_at.isoformat() if self.paid_at is not None else None,
            "credit_note_number": self.credit_note_number,
            "vat_breakdown": [dict(item) for item in self.vat_breakdown],
            "artifacts": dict(self.artifacts),
        }


def vat_breakdown(lines: list[InvoiceLine]) -> list[dict[str, str]]:
    """Group line nets by VAT rate and return exact decimal snapshots."""

    grouped: dict[Decimal, Decimal] = {}
    for line in lines:
        grouped[line.vat_rate] = grouped.get(line.vat_rate, Decimal("0.00")) + line.line_total
    result: list[dict[str, str]] = []
    for rate in sorted(grouped, reverse=True):
        taxable = q2(grouped[rate])
        result.append(
            {
                "rate": format(rate, "f"),
                "taxable_amount": format(taxable, "f"),
                "tax_amount": format(q2(taxable * rate), "f"),
            }
        )
    return result


def orm_to_invoice(orm: InvoiceORM) -> Invoice:
    return Invoice(
        invoice_number=orm.invoice_number,
        issue_date=orm.issue_date,
        due_date=orm.due_date,
        delivery_date=orm.delivery_date,
        currency=orm.currency,
        seller=InvoiceParty(
            name=orm.seller_name,
            street=orm.seller_street,
            city=orm.seller_city,
            postal_code=orm.seller_postal_code,
            country_code=orm.seller_country_code,
            vat_id=orm.seller_vat_id,
            email=orm.seller_email,
            telephone=orm.seller_phone,
            contact_name=orm.seller_contact_name,
        ),
        buyer=InvoiceParty(
            name=orm.buyer_name,
            street=orm.buyer_street,
            city=orm.buyer_city,
            postal_code=orm.buyer_postal_code,
            country_code=orm.buyer_country_code,
            vat_id=orm.buyer_vat_id,
            email=orm.buyer_email,
        ),
        buyer_reference=orm.buyer_reference,
        order_reference=orm.order_reference,
        lines=[
            InvoiceLine(
                line_id=line.line_id,
                name=line.description,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
                vat_rate=line.vat_rate,
            )
            for line in orm.lines
        ],
        total_net=orm.total_net,
        total_vat=orm.total_vat,
        total_gross=orm.total_gross,
        payment_terms=orm.payment_terms,
        iban=orm.iban,
    )


def _stored(orm: InvoiceORM) -> StoredInvoice:
    return StoredInvoice(
        id=orm.id,
        offer_id=orm.offer_id,
        invoice=orm_to_invoice(orm),
        status=orm.status,
        paid_amount=orm.paid_amount,
        credited_amount=orm.credited_amount,
        paid_at=orm.paid_at,
        credit_note_number=orm.credit_note_number,
        vat_breakdown=tuple(dict(item) for item in (orm.vat_breakdown or [])),
        artifacts=dict(orm.artifacts or {}),
    )


class InvoiceRepository:
    """Persist invoices while holding a row lock on the yearly counter."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_offer(self, offer_id: UUID) -> StoredInvoice | None:
        row = (
            (await self.session.execute(select(InvoiceORM).where(InvoiceORM.offer_id == offer_id)))
            .scalars()
            .first()
        )
        return _stored(row) if row is not None else None

    async def get_by_number(self, invoice_number: str) -> StoredInvoice | None:
        row = (
            (
                await self.session.execute(
                    select(InvoiceORM).where(InvoiceORM.invoice_number == invoice_number)
                )
            )
            .scalars()
            .first()
        )
        return _stored(row) if row is not None else None

    async def get_by_id(self, invoice_id: UUID) -> StoredInvoice | None:
        row = (
            (await self.session.execute(select(InvoiceORM).where(InvoiceORM.id == invoice_id)))
            .scalars()
            .first()
        )
        return _stored(row) if row is not None else None

    def _list_statement(
        self,
        *,
        owner: str | None = None,
        status: InvoiceStatus | None = None,
    ) -> Select[Any]:
        statement = select(InvoiceORM)
        if owner is not None:
            statement = statement.join(
                CustomerOfferORM,
                InvoiceORM.offer_id == CustomerOfferORM.id,
            ).where(CustomerOfferORM.owner == owner)
        if status is not None:
            statement = statement.where(InvoiceORM.status == status)
        return statement

    async def list(
        self,
        *,
        owner: str | None = None,
        status: InvoiceStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[StoredInvoice]:
        rows = (
            (
                await self.session.execute(
                    self._list_statement(owner=owner, status=status)
                    .order_by(InvoiceORM.issue_date.desc(), InvoiceORM.invoice_number.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return [_stored(row) for row in rows]

    async def count(
        self,
        *,
        owner: str | None = None,
        status: InvoiceStatus | None = None,
    ) -> int:
        statement = self._list_statement(owner=owner, status=status)
        count_statement = select(func.count()).select_from(statement.subquery())
        return int((await self.session.execute(count_statement)).scalar_one())

    async def events(self, invoice_id: UUID) -> builtins.list[dict[str, Any]]:
        return [
            event.as_dict()
            for event in await InvoiceEventRepository(self.session).list_for_invoice(invoice_id)
        ]

    async def verify_event_chain(self, invoice_id: UUID) -> bool:
        return await InvoiceEventRepository(self.session).verify_chain(invoice_id)

    async def create(
        self,
        invoice: Invoice,
        *,
        offer_id: UUID,
        actor: str | None = None,
    ) -> StoredInvoice:
        """Allocate the next number and persist the invoice in one transaction.

        The counter row is locked until commit, so a rollback also rolls back
        the consumed number and the sequence remains gapless.
        """

        existing = await self.get_by_offer(offer_id)
        if existing is not None:
            raise InvoiceRepositoryConflict(
                f"offer {offer_id} already has invoice {existing.invoice.invoice_number}"
            )

        try:
            number, sequence = await self._allocate_number(invoice.issue_date.year)
            numbered = invoice.model_copy(update={"invoice_number": number})
            row = self._to_orm(numbered, offer_id=offer_id, sequence=sequence)
            self.session.add(row)
            await self.session.flush()
            await InvoiceEventRepository(self.session).append(
                row.id,
                "ISSUED",
                actor=actor,
                amount=row.total_gross,
                reference=row.invoice_number,
                payload={
                    "offer_id": str(offer_id),
                    "invoice_year": row.invoice_year,
                    "sequence_number": row.sequence_number,
                    "total_net": format(row.total_net, "f"),
                    "total_vat": format(row.total_vat, "f"),
                    "total_gross": format(row.total_gross, "f"),
                },
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise InvoiceRepositoryConflict(f"invoice number or offer conflict: {exc}") from exc
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, InvoiceRepositoryError):
                raise
            raise InvoiceRepositoryError(f"invoice persistence failed: {exc}") from exc
        return _stored(row)

    async def _allocate_number(self, year: int) -> tuple[str, int]:
        if not 2000 <= year <= 2200:
            raise InvoiceRepositoryError(f"invoice year {year} is outside the supported range")
        await self.session.execute(
            insert(InvoiceNumberSequence)
            .values(year=year, last_value=0)
            .on_conflict_do_nothing(index_elements=[InvoiceNumberSequence.year])
        )
        row = (
            await self.session.execute(
                select(InvoiceNumberSequence).where(InvoiceNumberSequence.year == year).with_for_update()
            )
        ).scalar_one()
        if row.last_value >= _MAX_SEQUENCE:
            raise InvoiceNumberExhaustedError(
                f"invoice number space INV-{year}-{_MAX_SEQUENCE:04d} is exhausted"
            )
        row.last_value += 1
        await self.session.flush()
        return f"INV-{year}-{row.last_value:04d}", row.last_value

    def _to_orm(self, invoice: Invoice, *, offer_id: UUID, sequence: int) -> InvoiceORM:
        breakdown = vat_breakdown(invoice.lines)
        row = InvoiceORM(
            offer_id=offer_id,
            invoice_number=invoice.invoice_number,
            invoice_year=invoice.issue_date.year,
            sequence_number=sequence,
            status=InvoiceStatus.ISSUED,
            currency=invoice.currency,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            delivery_date=invoice.delivery_date,
            seller_name=invoice.seller.name,
            seller_street=invoice.seller.street or "",
            seller_city=invoice.seller.city or "",
            seller_postal_code=invoice.seller.postal_code or "",
            seller_country_code=invoice.seller.country_code,
            seller_vat_id=invoice.seller.vat_id,
            seller_email=str(invoice.seller.email) if invoice.seller.email else None,
            seller_phone=invoice.seller.telephone,
            seller_contact_name=invoice.seller.contact_name,
            buyer_name=invoice.buyer.name,
            buyer_street=invoice.buyer.street or "",
            buyer_city=invoice.buyer.city or "",
            buyer_postal_code=invoice.buyer.postal_code or "",
            buyer_country_code=invoice.buyer.country_code,
            buyer_vat_id=invoice.buyer.vat_id,
            buyer_email=str(invoice.buyer.email) if invoice.buyer.email else None,
            buyer_reference=invoice.buyer_reference,
            order_reference=invoice.order_reference,
            payment_terms=invoice.payment_terms,
            iban=invoice.iban,
            total_net=invoice.total_net,
            total_vat=invoice.total_vat,
            total_gross=invoice.total_gross,
            vat_breakdown=breakdown,
        )
        row.lines = [
            InvoiceLineORM(
                line_id=line.line_id,
                description=line.name,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
                vat_rate=line.vat_rate,
            )
            for line in invoice.lines
        ]
        row.vat_splits = [
            InvoiceVatSplit(
                rate=Decimal(item["rate"]),
                taxable_amount=Decimal(item["taxable_amount"]),
                tax_amount=Decimal(item["tax_amount"]),
            )
            for item in breakdown
        ]
        return row

    async def attach_artifacts(
        self,
        invoice_id: UUID,
        artifacts: dict[str, Any],
    ) -> StoredInvoice:
        row = await self.session.get(InvoiceORM, invoice_id)
        if row is None:
            raise InvoiceRepositoryError(f"invoice {invoice_id} does not exist")
        row.artifacts = dict(artifacts)
        await self.session.commit()
        return _stored(row)

    async def record_payment(
        self,
        invoice_id: UUID,
        amount: Decimal,
        *,
        actor: str | None = None,
        reference: str | None = None,
    ) -> StoredInvoice:
        """Add a payment, append its event and derive the payment status."""

        row = await self._get_row(invoice_id, lock=True)
        value = q2(amount)
        if value <= 0:
            raise InvoiceRepositoryError("payment amount must be positive")
        if row.paid_amount + row.credited_amount + value > row.total_gross:
            raise InvoiceRepositoryError("payment exceeds the outstanding invoice amount")
        row.paid_amount = q2(row.paid_amount + value)
        outstanding = q2(row.total_gross - row.credited_amount - row.paid_amount)
        row.status = InvoiceStatus.PAID if outstanding <= 0 else InvoiceStatus.PARTIALLY_PAID
        if row.status == InvoiceStatus.PAID:
            row.paid_at = datetime.now(UTC)
        await InvoiceEventRepository(self.session).append(
            invoice_id,
            "PAYMENT_RECORDED",
            actor=actor,
            amount=value,
            reference=reference,
            payload={
                "invoice_number": row.invoice_number,
                "paid_amount": format(row.paid_amount, "f"),
                "outstanding_amount": format(outstanding, "f"),
                "status": row.status.value,
            },
        )
        await self.session.commit()
        return _stored(row)

    async def record_credit(
        self,
        invoice_id: UUID,
        amount: Decimal,
        *,
        credit_note_number: str,
        credit_note_id: UUID | None = None,
        actor: str | None = None,
        reference: str | None = None,
        commit: bool = True,
    ) -> StoredInvoice:
        """Record a credit note against the invoice without reusing the number."""

        row = await self._get_row(invoice_id, lock=True)
        value = q2(amount)
        if value <= 0:
            raise InvoiceRepositoryError("credit amount must be positive")
        if row.paid_amount + row.credited_amount + value > row.total_gross:
            raise InvoiceRepositoryError("credit exceeds the outstanding invoice amount")
        row.credited_amount = q2(row.credited_amount + value)
        row.credit_note_number = credit_note_number[:64]
        outstanding = q2(row.total_gross - row.credited_amount - row.paid_amount)
        row.status = InvoiceStatus.CREDITED if outstanding <= 0 else InvoiceStatus.PARTIALLY_CREDITED
        await InvoiceEventRepository(self.session).append(
            invoice_id,
            "CREDIT_ISSUED",
            actor=actor,
            amount=value,
            reference=reference or credit_note_number,
            payload={
                "invoice_number": row.invoice_number,
                "credit_note_number": credit_note_number,
                "credit_note_id": str(credit_note_id) if credit_note_id is not None else None,
                "credited_amount": format(row.credited_amount, "f"),
                "outstanding_amount": format(outstanding, "f"),
                "status": row.status.value,
            },
        )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return _stored(row)

    async def mark_overdue(self, invoice_id: UUID, *, today: date | None = None) -> StoredInvoice:
        row = await self._get_row(invoice_id, lock=True)
        current = today or datetime.now(UTC).date()
        if row.status in {InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID} and row.due_date < current:
            previous = row.status
            row.status = InvoiceStatus.OVERDUE
            await InvoiceEventRepository(self.session).append(
                invoice_id,
                "STATUS_CHANGED",
                reference=row.invoice_number,
                payload={
                    "previous_status": previous.value,
                    "status": row.status.value,
                },
            )
            await self.session.commit()
        return _stored(row)

    async def _get_row(self, invoice_id: UUID, *, lock: bool = False) -> InvoiceORM:
        if lock:
            row = (
                (
                    await self.session.execute(
                        select(InvoiceORM).where(InvoiceORM.id == invoice_id).with_for_update()
                    )
                )
                .scalars()
                .first()
            )
        else:
            row = await self.session.get(InvoiceORM, invoice_id)
        if row is None:
            raise InvoiceRepositoryError(f"invoice {invoice_id} does not exist")
        return row
