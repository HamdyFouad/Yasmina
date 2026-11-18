"""Streaming CSV and XLSX exports of the GoBD audit chain."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any, BinaryIO, cast
from xml.sax.saxutils import escape

from src.repositories.audit_repository import AuditLogRepository, AuditLogView

__all__ = [
    "AUDIT_EXPORT_HEADERS",
    "build_xlsx",
    "csv_stream",
    "export_filename",
    "stream_file",
]

AUDIT_EXPORT_HEADERS = (
    "ID",
    "Occurred at",
    "Event type",
    "Actor",
    "Entity type",
    "Entity ID",
    "Payload",
    "Previous hash",
    "Entry hash",
    "Signature",
)


def export_filename(format_name: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"yasmina-audit-logs-{stamp}.{format_name}"


def _csv_value(value: str) -> str:
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _row_values(row: AuditLogView) -> tuple[str, ...]:
    return (
        str(row.id),
        row.occurred_at.isoformat(),
        row.event_type,
        _csv_value(row.actor),
        _csv_value(row.entity_type),
        _csv_value(row.entity_id),
        _csv_value(json.dumps(row.payload, ensure_ascii=False, sort_keys=True)),
        row.prev_hash or "",
        row.entry_hash,
        row.signature or "",
    )


async def csv_stream(
    repository: AuditLogRepository,
    *,
    batch_size: int = 1000,
    filters: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(AUDIT_EXPORT_HEADERS)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    async for row in repository.iter_export(batch_size=batch_size, **(filters or {})):
        writer.writerow(_row_values(row))
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def _xml_text(value: str) -> str:
    cleaned = "".join(character for character in value if character in "\t\n\r" or ord(character) >= 0x20)
    return escape(cleaned)


def _column(index: int) -> str:
    name = ""
    value = index
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _inline_cell(reference: str, value: str, *, header: bool = False) -> str:
    style = ' s="1"' if header else ""
    return (
        f'<c r="{reference}"{style} t="inlineStr"><is><t xml:space="preserve">{_xml_text(value)}</t></is></c>'
    )


def _number_cell(reference: str, value: int) -> str:
    return f'<c r="{reference}"><v>{value}</v></c>'


def _sheet_row(row_number: int, values: tuple[str, ...], *, header: bool = False) -> str:
    cells: list[str] = []
    for index, value in enumerate(values, start=1):
        reference = f"{_column(index)}{row_number}"
        if index == 1 and not header:
            cells.append(_number_cell(reference, int(value)))
        else:
            cells.append(_inline_cell(reference, value, header=header))
    return f'<row r="{row_number}">{"".join(cells)}</row>'


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Audit Logs" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
</styleSheet>"""


async def build_xlsx(
    repository: AuditLogRepository,
    *,
    batch_size: int = 1000,
    filters: dict[str, Any] | None = None,
) -> BinaryIO:
    """Build a valid XLSX workbook in a spooled temporary file."""

    target = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")  # noqa: SIM115
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", _CONTENT_TYPES)
        workbook.writestr("_rels/.rels", _ROOT_RELS)
        workbook.writestr("xl/workbook.xml", _WORKBOOK)
        workbook.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        workbook.writestr("xl/styles.xml", _STYLES)
        with workbook.open("xl/worksheets/sheet1.xml", "w") as sheet:
            sheet.write(
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                b'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                b'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>'
            )
            sheet.write(_sheet_row(1, AUDIT_EXPORT_HEADERS, header=True).encode())
            row_number = 1
            async for row in repository.iter_export(batch_size=batch_size, **(filters or {})):
                row_number += 1
                sheet.write(_sheet_row(row_number, _row_values(row)).encode())
            sheet.write(b"</sheetData></worksheet>")
    target.seek(0)
    return cast(BinaryIO, target)


def stream_file(handle: BinaryIO, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    try:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        handle.close()
