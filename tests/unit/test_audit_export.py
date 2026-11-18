"""Audit CSV/XLSX streaming export tests."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from typing import Any

from src.repositories.audit_repository import AuditLogView
from src.services.audit_export import AUDIT_EXPORT_HEADERS, build_xlsx, csv_stream

ROW = AuditLogView(
    id=7,
    occurred_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
    event_type="USER_LOGIN_SUCCEEDED",
    actor="=admin",
    entity_type="user",
    entity_id="admin",
    payload={"ip_address": "127.0.0.1", "note": "quoted, value"},
    prev_hash="a" * 64,
    entry_hash="b" * 64,
    signature=None,
)


class FakeAuditRepository:
    async def iter_export(self, *, batch_size: int = 1000, **filters: Any):
        yield ROW


async def test_csv_export_streams_header_and_sanitized_rows() -> None:
    chunks = [chunk async for chunk in csv_stream(FakeAuditRepository())]
    body = "".join(chunks)

    assert body.startswith(",".join(AUDIT_EXPORT_HEADERS))
    assert "USER_LOGIN_SUCCEEDED" in body
    assert "'=admin" in body
    assert '"quoted, value"' in body


async def test_xlsx_export_is_a_valid_workbook_with_rows() -> None:
    handle = await build_xlsx(FakeAuditRepository())
    try:
        with zipfile.ZipFile(handle) as workbook:
            names = set(workbook.namelist())
            assert "[Content_Types].xml" in names
            assert "xl/workbook.xml" in names
            assert "xl/worksheets/sheet1.xml" in names
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode()
        assert "USER_LOGIN_SUCCEEDED" in sheet
        assert 'r="A2"' in sheet
        assert "quoted, value" in sheet
    finally:
        handle.close()


async def test_xlsx_export_handles_empty_chain() -> None:
    class EmptyRepository:
        async def iter_export(self, *, batch_size: int = 1000, **filters: Any):
            if False:
                yield ROW

    handle = await build_xlsx(EmptyRepository())
    try:
        with zipfile.ZipFile(handle) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode()
        assert "Audit Logs" not in sheet
        assert "<sheetData>" in sheet
    finally:
        handle.close()
