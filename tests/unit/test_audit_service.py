"""Hash-chain helper tests for the audit service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.models.enums import AuditEventType
from src.services.audit_service import AuditService, audit_entry_hash


class FakeResult:
    def __init__(self, value):  # type: ignore[no-untyped-def]
        self.value = value

    def scalars(self):  # type: ignore[no-untyped-def]
        return self

    def first(self):  # type: ignore[no-untyped-def]
        return self.value


class FakeSession:
    def __init__(self, previous):  # type: ignore[no-untyped-def]
        self.previous = previous
        self.added = []
        self.commits = 0
        self.calls = 0

    async def execute(self, statement):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return FakeResult(None)
        return FakeResult(self.previous)

    def add(self, entry):  # type: ignore[no-untyped-def]
        self.added.append(entry)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


class Previous:
    entry_hash = "a" * 64


def test_audit_service_links_previous_hash() -> None:
    session = FakeSession(Previous())

    entry = asyncio.run(
        AuditService(session).record_clamav(
            actor="sales-user",
            upload_id="upload-1",
            filename="tender.X83",
            clean=True,
            object_name="tenders/1/tender.X83",
        )
    )

    assert entry.prev_hash == "a" * 64
    assert len(entry.entry_hash) == 64
    assert session.added == [entry]
    assert session.commits == 1
    assert entry.event_type is AuditEventType.CLAMAV_SCAN_CLEAN


def test_audit_service_uses_genesis_hash_when_empty() -> None:
    session = FakeSession(None)

    entry = asyncio.run(
        AuditService(session).record_clamav(
            actor="sales-user",
            upload_id="upload-1",
            filename="tender.X83",
            clean=False,
            signature="Eicar",
        )
    )

    assert entry.prev_hash is None
    assert entry.event_type is AuditEventType.CLAMAV_SCAN_REJECTED


def test_audit_hash_is_deterministic_and_order_independent() -> None:
    first = audit_entry_hash(
        occurred_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
        event_type=AuditEventType.CLAMAV_SCAN_CLEAN,
        actor="sales-user",
        entity_type="tender_upload",
        entity_id="upload-1",
        payload={"b": 2, "a": 1},
        prev_hash=None,
    )
    second = audit_entry_hash(
        occurred_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
        event_type=AuditEventType.CLAMAV_SCAN_CLEAN,
        actor="sales-user",
        entity_type="tender_upload",
        entity_id="upload-1",
        payload={"a": 1, "b": 2},
        prev_hash=None,
    )

    assert first == second
    assert len(first) == 64


def test_audit_hash_changes_with_chain_and_payload() -> None:
    base = dict(
        occurred_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
        event_type=AuditEventType.CLAMAV_SCAN_REJECTED,
        actor="sales-user",
        entity_type="tender_upload",
        entity_id="upload-1",
        payload={"filename": "x.x83", "signature": "Eicar"},
        prev_hash=None,
    )
    first = audit_entry_hash(**base)
    chained = audit_entry_hash(**{**base, "prev_hash": first})
    changed = audit_entry_hash(**{**base, "payload": {"filename": "y.x83"}})

    assert first != chained
    assert first != changed
