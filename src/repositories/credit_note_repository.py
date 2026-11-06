"""Dedicated credit-note generation with its own gapless numbering sequence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import (
    CreditNote,
    CreditNoteNumberSequence,
)
from src.models.orm import (
    Invoice as InvoiceORM,
)
from src.models.schemas import q2
from src.repositories.invoice_repository import InvoiceRepository, InvoiceRepositoryError

__all__ = [
    "CreditNoteNumberExhaustedError",
    "CreditNoteRepository",
    "CreditNoteRepositoryError",
    "StoredCreditNote",
]

_MAX_SEQUENCE = 9999


class CreditNoteRepositoryError(ValueError):
    """Raised when a credit note cannot be generated."""


class CreditNoteNumberExhaustedError(CreditNoteRepositoryError):
    """Raised when the yearly GS-YYYY-XXXX space is exhausted."""


def _rate_label(rate: Decimal) -> str:
    percent = (rate * Decimal(100)).normalize()
    return f"{percent:.1f}".replace(".", ",") + " %"


@dataclass(frozen=True, slots=True)
class StoredCreditNote:
    id: UUID
    invoice_id: UUID
    credit_note_number: str
    issue_date: date
    status: str
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    vat_breakdown: tuple[dict[str, str], ...]
    lines: tuple[dict[str, Any], ...]
    artifacts: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "invoice_id": str(self.invoice_id),
            "credit_note_number": self.credit_note_number,
            "issue_date": self.issue_date.isoformat(),
            "status": self.status,
            "total_net": format(self.total_net, "f"),
            "total_vat": format(self.total_vat, "f"),
            "total_gross": format(self.total_gross, "f"),
            "vat_breakdown": [dict(item) for item in self.vat_breakdown],
            "lines": [dict(line) for line in self.lines],
            "artifacts": dict(self.artifacts),
        }


def _stored(row: CreditNote) -> StoredCreditNote:
    return StoredCreditNote(
        id=row.id,
        invoice_id=row.invoice_id,
        credit_note_number=row.credit_note_number,
        issue_date=row.issue_date,
        status=row.status,
        total_net=row.total_net,
        total_vat=row.total_vat,
        total_gross=row.total_gross,
        vat_breakdown=tuple(dict(item) for item in (row.vat_breakdown or [])),
        lines=tuple(dict(line) for line in (row.lines or [])),
        artifacts=dict(row.artifacts or {}),
    )


class CreditNoteRepository:
    """Generate and apply a credit note atomically with the invoice update."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self,
        invoice_id: UUID,
        *,
        amount: Decimal | None = None,
        reason: str,
        issue_date: date | None = None,
        actor: str | None = None,
    ) -> StoredCreditNote:
        reason = reason.strip()
        if not reason:
            raise CreditNoteRepositoryError("credit note reason is required")
        try:
            invoice = (
                (
                    await self.session.execute(
                        select(InvoiceORM).where(InvoiceORM.id == invoice_id).with_for_update()
                    )
                )
                .scalars()
                .first()
            )
            if invoice is None:
                raise CreditNoteRepositoryError(f"invoice {invoice_id} does not exist")
            outstanding = q2(invoice.total_gross - invoice.paid_amount - invoice.credited_amount)
            value = q2(amount) if amount is not None else outstanding
            if value <= 0:
                raise CreditNoteRepositoryError("credit amount must be positive")
            if value > outstanding:
                raise CreditNoteRepositoryError("credit exceeds the outstanding invoice amount")

            lines, breakdown, total_net, total_vat = self._allocate(invoice, value)
            current = issue_date or datetime.now(UTC).date()
            number, sequence = await self._allocate_number(current.year)
            note = CreditNote(
                id=uuid4(),
                invoice_id=invoice_id,
                credit_note_number=number,
                credit_note_year=current.year,
                sequence_number=sequence,
                status="APPLIED",
                issue_date=current,
                reason=reason[:4000],
                total_net=total_net,
                total_vat=total_vat,
                total_gross=value,
                vat_breakdown=breakdown,
                lines=lines,
                artifacts={},
            )
            self.session.add(note)
            await self.session.flush()
            await InvoiceRepository(self.session).record_credit(
                invoice_id,
                value,
                credit_note_number=number,
                credit_note_id=note.id,
                actor=actor,
                reference=number,
                commit=False,
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise CreditNoteRepositoryError(f"credit note persistence failed: {exc}") from exc
        except (CreditNoteRepositoryError, InvoiceRepositoryError):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise CreditNoteRepositoryError(f"credit note generation failed: {exc}") from exc
        return _stored(note)

    async def get(self, credit_note_id: UUID) -> StoredCreditNote | None:
        row = await self.session.get(CreditNote, credit_note_id)
        return _stored(row) if row is not None else None

    async def list_for_invoice(self, invoice_id: UUID) -> list[StoredCreditNote]:
        rows = (
            (
                await self.session.execute(
                    select(CreditNote)
                    .where(CreditNote.invoice_id == invoice_id)
                    .order_by(CreditNote.sequence_number)
                )
            )
            .scalars()
            .all()
        )
        return [_stored(row) for row in rows]

    async def _allocate_number(self, year: int) -> tuple[str, int]:
        if not 2000 <= year <= 2200:
            raise CreditNoteRepositoryError(f"credit note year {year} is outside the supported range")
        await self.session.execute(
            insert(CreditNoteNumberSequence)
            .values(year=year, last_value=0)
            .on_conflict_do_nothing(index_elements=[CreditNoteNumberSequence.year])
        )
        row = (
            await self.session.execute(
                select(CreditNoteNumberSequence)
                .where(CreditNoteNumberSequence.year == year)
                .with_for_update()
            )
        ).scalar_one()
        if row.last_value >= _MAX_SEQUENCE:
            raise CreditNoteNumberExhaustedError(
                f"credit note number space GS-{year}-{_MAX_SEQUENCE:04d} is exhausted"
            )
        row.last_value += 1
        await self.session.flush()
        return f"GS-{year}-{row.last_value:04d}", row.last_value

    def _allocate(
        self,
        invoice: InvoiceORM,
        amount: Decimal,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], Decimal, Decimal]:
        buckets = [
            (
                Decimal(str(item["rate"])),
                Decimal(str(item["taxable_amount"])),
                Decimal(str(item["tax_amount"])),
            )
            for item in (invoice.vat_breakdown or [])
        ]
        if not buckets:
            raise CreditNoteRepositoryError("invoice has no VAT breakdown to credit")
        total_invoice_gross = q2(sum((base + tax for _, base, tax in buckets), Decimal("0.00")))
        if total_invoice_gross <= 0:
            raise CreditNoteRepositoryError("invoice gross total must be positive")

        shares: list[tuple[Decimal, Decimal]] = []
        remaining = amount
        for index, (rate, base, tax) in enumerate(buckets):
            gross = q2(base + tax)
            if index == len(buckets) - 1:
                share = remaining
            else:
                share = min(remaining, q2(amount * gross / total_invoice_gross))
            shares.append((rate, share))
            remaining = q2(remaining - share)
            if remaining <= 0 and index < len(buckets) - 1:
                for _ in range(index + 1, len(buckets)):
                    shares.append((buckets[_][0], Decimal("0.00")))
                break

        lines: list[dict[str, Any]] = []
        breakdown: list[dict[str, str]] = []
        total_net = Decimal("0.00")
        total_vat = Decimal("0.00")
        for rate, gross_share in shares:
            if gross_share <= 0:
                continue
            tax = q2(gross_share * rate / (Decimal(1) + rate))
            net = q2(gross_share - tax)
            lines.append(
                {
                    "description": f"Korrektur zu Rechnung {invoice.invoice_number} ({_rate_label(rate)})",
                    "net_amount": format(net, "f"),
                    "vat_rate": format(rate, "f"),
                    "tax_amount": format(tax, "f"),
                    "gross_amount": format(gross_share, "f"),
                }
            )
            breakdown.append(
                {
                    "rate": format(rate, "f"),
                    "taxable_amount": format(net, "f"),
                    "tax_amount": format(tax, "f"),
                }
            )
            total_net += net
            total_vat += tax

        total_net = q2(total_net)
        total_vat = q2(total_vat)
        difference = q2(amount - total_net - total_vat)
        if difference:
            if not lines:
                raise CreditNoteRepositoryError("credit amount cannot be allocated")
            last_net = q2(Decimal(lines[-1]["net_amount"]) + difference)
            lines[-1]["net_amount"] = format(last_net, "f")
            lines[-1]["gross_amount"] = format(q2(last_net + Decimal(lines[-1]["tax_amount"])), "f")
            breakdown[-1]["taxable_amount"] = format(last_net, "f")
            total_net = q2(total_net + difference)
        return lines, breakdown, total_net, total_vat
