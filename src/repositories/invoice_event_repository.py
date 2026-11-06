"""Append-only, hash-chained invoice lifecycle event persistence (GoBD)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Invoice as InvoiceORM
from src.models.orm import InvoiceEvent

__all__ = [
    "InvoiceEventRepository",
    "InvoiceEventRepositoryError",
    "InvoiceEventView",
    "invoice_event_hash",
]

_ZERO_HASH = "0" * 64


class InvoiceEventRepositoryError(ValueError):
    """Raised when an invoice event cannot be appended."""


@dataclass(frozen=True, slots=True)
class InvoiceEventView:
    id: UUID
    invoice_id: UUID
    sequence: int
    event_type: str
    occurred_at: datetime
    actor: str | None
    amount: Decimal | None
    reference: str | None
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "invoice_id": str(self.invoice_id),
            "sequence": self.sequence,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "actor": self.actor,
            "amount": format(self.amount, "f") if self.amount is not None else None,
            "reference": self.reference,
            "payload": dict(self.payload),
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }


def invoice_event_hash(
    *,
    invoice_id: UUID,
    sequence: int,
    event_type: str,
    occurred_at: datetime,
    actor: str | None,
    amount: Decimal | None,
    reference: str | None,
    payload: dict[str, Any],
    prev_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "invoice_id": str(invoice_id),
            "sequence": sequence,
            "event_type": event_type,
            "occurred_at": occurred_at.astimezone(UTC).isoformat(),
            "actor": actor,
            "amount": format(amount, "f") if amount is not None else None,
            "reference": reference,
            "payload": payload,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _view(row: InvoiceEvent) -> InvoiceEventView:
    return InvoiceEventView(
        id=row.id,
        invoice_id=row.invoice_id,
        sequence=row.sequence,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        actor=row.actor,
        amount=row.amount,
        reference=row.reference,
        payload=dict(row.payload or {}),
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
    )


class InvoiceEventRepository:
    """Append events without ever mutating an existing event row."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        invoice_id: UUID,
        event_type: str,
        *,
        actor: str | None = None,
        amount: Decimal | None = None,
        reference: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        commit: bool = False,
    ) -> InvoiceEventView:
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
            raise InvoiceEventRepositoryError(f"invoice {invoice_id} does not exist")
        latest = (
            (
                await self.session.execute(
                    select(InvoiceEvent)
                    .where(InvoiceEvent.invoice_id == invoice_id)
                    .order_by(desc(InvoiceEvent.sequence))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        current = occurred_at or datetime.now(UTC)
        sequence = (latest.sequence + 1) if latest is not None else 1
        prev_hash = latest.entry_hash if latest is not None else _ZERO_HASH
        details = dict(payload or {})
        entry_hash = invoice_event_hash(
            invoice_id=invoice_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=current,
            actor=actor,
            amount=amount,
            reference=reference,
            payload=details,
            prev_hash=prev_hash,
        )
        row = InvoiceEvent(
            invoice_id=invoice_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=current,
            actor=actor,
            amount=amount,
            reference=reference,
            payload=details,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self.session.add(row)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return _view(row)

    async def list_for_invoice(self, invoice_id: UUID) -> list[InvoiceEventView]:
        rows = (
            (
                await self.session.execute(
                    select(InvoiceEvent)
                    .where(InvoiceEvent.invoice_id == invoice_id)
                    .order_by(InvoiceEvent.sequence)
                )
            )
            .scalars()
            .all()
        )
        return [_view(row) for row in rows]

    async def verify_chain(self, invoice_id: UUID) -> bool:
        events = await self.list_for_invoice(invoice_id)
        prev_hash = _ZERO_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.prev_hash != prev_hash:
                return False
            expected = invoice_event_hash(
                invoice_id=event.invoice_id,
                sequence=event.sequence,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                actor=event.actor,
                amount=event.amount,
                reference=event.reference,
                payload=event.payload,
                prev_hash=event.prev_hash,
            )
            if expected != event.entry_hash:
                return False
            prev_hash = event.entry_hash
        return True
