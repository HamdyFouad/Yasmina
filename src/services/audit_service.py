"""Append-only, hash-chained audit log writer."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AuditEventType
from src.models.orm import AuditLog

__all__ = ["AuditService", "AuditServiceError", "audit_entry_hash"]


class AuditServiceError(RuntimeError):
    """Raised when an audit entry cannot be appended."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def audit_entry_hash(
    *,
    occurred_at: datetime,
    event_type: AuditEventType | str,
    actor: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
    prev_hash: str | None,
) -> str:
    """Canonical SHA-256 over the immutable audit fields (excluding the DB id)."""

    canonical = {
        "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
        "event_type": getattr(event_type, "value", str(event_type)),
        "actor": actor,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "payload": _jsonable(payload),
        "prev_hash": prev_hash,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AuditService:
    """Serializes writers with a PostgreSQL advisory lock to keep one chain."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_clamav(
        self,
        *,
        actor: str,
        upload_id: str,
        filename: str,
        clean: bool,
        signature: str | None = None,
        object_name: str | None = None,
    ) -> AuditLog:
        event = AuditEventType.CLAMAV_SCAN_CLEAN if clean else AuditEventType.CLAMAV_SCAN_REJECTED
        payload: dict[str, Any] = {
            "filename": filename,
            "clean": clean,
            "signature": signature,
            "object_name": object_name,
        }
        return await self.append(
            event_type=event,
            actor=actor,
            entity_type="tender_upload",
            entity_id=upload_id,
            payload=payload,
        )

    async def append(
        self,
        *,
        event_type: AuditEventType,
        actor: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        commit: bool = True,
    ) -> AuditLog:
        occurred_at = datetime.now(timezone.utc)
        try:
            # One chain: serialize concurrent appenders inside this transaction.
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('yasmina_audit_logs'))")
            )
            previous = (
                await self.session.execute(
                    select(AuditLog).order_by(AuditLog.id.desc()).limit(1)
                )
            ).scalars().first()
            prev_hash = previous.entry_hash if previous is not None else None
            entry_hash = audit_entry_hash(
                occurred_at=occurred_at,
                event_type=event_type,
                actor=actor,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                prev_hash=prev_hash,
            )
            entry = AuditLog(
                occurred_at=occurred_at,
                event_type=event_type,
                actor=actor[:128],
                entity_type=entity_type[:64],
                entity_id=entity_id[:64],
                payload=_jsonable(payload),
                prev_hash=prev_hash,
                entry_hash=entry_hash,
            )
            self.session.add(entry)
            if commit:
                await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            raise AuditServiceError(f"cannot append audit entry: {exc}") from exc
        return entry
