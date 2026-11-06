"""PDF LV parser using Docling table extraction.

Docling is an optional dependency and is imported lazily. The parser accepts an
injected converter for tests and only relies on Docling's public document/table
surface: ``converter.convert(...)``, ``result.document.tables`` and
``table.export_to_dataframe(doc=...)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.models.schemas import TenderItem
from src.parsers.normalizers import (
    normalize_color,
    normalize_glove_size,
    normalize_material,
    normalize_unit_of_measure,
)

__all__ = ["PdfParseError", "PdfParserUnavailable", "PdfParseResult", "parse_pdf"]

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "position": ("pos", "pos.", "position", "oz", "ordnungszahl", "nr", "nr.", "lfd", "lfd. nr."),
    "lot": ("los", "los nr", "los-nr", "lot"),
    "quantity": ("menge", "menge (me)", "me", "me.", "quantity", "qty"),
    "unit": ("einheit", "einheit (eh)", "eh", "eh.", "unit", "einh", "einh."),
    "short_text": ("kurztext", "bezeichnung", "beschreibung", "text", "leistung", "artikel"),
    "long_text": ("langtext", "ausführliche beschreibung", "ausfuehrliche beschreibung"),
}

_SKIP_POSITIONS = {"summe", "gesamt", "gesamtbetrag", "zwischensumme", "übertrag", "uebertrag"}

_GLOVE_SIZE_RE = re.compile(
    r"\b(?:gr\.?|größe|groesse|size)\s*(xxl|xl|xs|m|l|s|\d{1,2}(?:[.,]\d)?)\b",
    re.IGNORECASE,
)
_PACK_RE = re.compile(
    r"(?:à|je|inhalt|packung|karton)\s*[:\-]?\s*(\d{1,3}(?:[.,]\d{3})*|\d+)",
    re.IGNORECASE,
)
_PACK_BEFORE_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})*|\d+)\s*(?:stk\.?|stück|stueck|st\.?)\s*(?:je|pro|/)\s*(?:karton|pack|packung)\b",
    re.IGNORECASE,
)


class PdfParserUnavailable(RuntimeError):
    """Raised when Docling is not installed."""


class PdfParseError(ValueError):
    """Raised when a PDF cannot be converted into structured LV rows."""


@dataclass(frozen=True)
class PdfParseResult:
    items: tuple[TenderItem, ...]
    warnings: tuple[str, ...] = ()
    table_count: int = 0
    converter_name: str | None = None


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical_header(value: Any) -> str | None:
    normalized = _clean(value).lower().rstrip(":").strip()
    for canonical, aliases in _HEADER_ALIASES.items():
        if normalized in aliases:
            return canonical
    return None


def _german_decimal(value: Any) -> Decimal | None:
    """Parse German quantity formats: ``1.234,56`` and ``10,5``."""

    text = _clean(value).replace(" ", "")
    if not text:
        return None
    text = re.sub(r"[^0-9,.\-]", "", text)
    if not text:
        return None
    try:
        if "." in text and "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", text):
            text = text.replace(".", "")
        return Decimal(text)
    except InvalidOperation:
        return None


def _table_records(table: Any, document: Any) -> list[dict[str, Any]]:
    """Extract rows from a Docling table using its public exports."""

    if hasattr(table, "export_to_dataframe"):
        try:
            frame = table.export_to_dataframe(doc=document)
            if hasattr(frame, "to_dict"):
                try:
                    records = frame.to_dict("records")
                except TypeError:
                    records = frame.to_dict()
                if records and isinstance(records[0], dict):
                    return [dict(record) for record in records]
        except Exception:
            # Fall through to the grid representation.
            pass

    grid = getattr(getattr(table, "data", None), "grid", None)
    if grid:
        rows: list[list[str]] = []
        for row in grid:
            rows.append([_clean(getattr(cell, "text", cell)) for cell in row])
        if not rows:
            return []
        headers = rows[0]
        return [dict(zip(headers, row, strict=False)) for row in rows[1:]]
    return []


def _headers(record: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key in record:
        canonical = _canonical_header(key)
        if canonical is not None:
            mapping[canonical] = key
    return mapping


def _row_value(record: dict[str, Any], headers: dict[str, str], field: str) -> str:
    key = headers.get(field)
    return _clean(record.get(key)) if key is not None else ""


def _decide_glove_size(text: str) -> GloveSize:
    match = _GLOVE_SIZE_RE.search(text)
    return normalize_glove_size(match.group(1)) if match else GloveSize.NOT_APPLICABLE


def _decide_pack_size(text: str) -> int | None:
    match = _PACK_RE.search(text) or _PACK_BEFORE_RE.search(text)
    if match is None:
        return None
    value = _german_decimal(match.group(1))
    if value is None or value <= 0 or value != value.to_integral_value():
        return None
    return int(value)


def _build_item(
    record: dict[str, Any],
    *,
    source: Path,
    table_index: int,
    row_index: int,
    warnings: list[str],
) -> TenderItem | None:
    headers = _headers(record)
    if "position" not in headers or "short_text" not in headers:
        return None

    position = _row_value(record, headers, "position")
    if not position or position.lower() in _SKIP_POSITIONS:
        return None

    short_text = _row_value(record, headers, "short_text")
    if not short_text:
        warnings.append(f"{source.name} table {table_index + 1} row {row_index + 1}: no description")
        return None

    quantity = _german_decimal(_row_value(record, headers, "quantity"))
    if quantity is None or quantity <= 0:
        warnings.append(
            f"{source.name} table {table_index + 1} row {row_index + 1}: "
            f"no positive quantity for position {position}"
        )
        return None

    unit = normalize_unit_of_measure(_row_value(record, headers, "unit"))
    if unit is UnitOfMeasure.NOT_APPLICABLE:
        warnings.append(f"{source.name} position {position}: unknown unit")

    long_text = _row_value(record, headers, "long_text") or None
    lot = _row_value(record, headers, "lot") or None
    combined = f"{short_text} {long_text or ''}"

    material: MaterialType | None = normalize_material(combined)
    color: StandardizedColor | None = normalize_color(combined)
    if material is MaterialType.NOT_APPLICABLE:
        material = None
    if color is StandardizedColor.UNKNOWN:
        color = None

    return TenderItem(
        position_number=position[:64],
        lot=lot[:64] if lot else None,
        short_text=short_text[:512],
        long_text=long_text[:8000] if long_text else None,
        quantity=quantity,
        unit=unit,
        glove_size=_decide_glove_size(combined),
        material=material,
        color=color,
        pack_size=_decide_pack_size(combined),
        source_reference=f"{source.name}#table{table_index + 1}:row{row_index + 1}",
        attributes={
            "pdf_table_index": table_index,
            "pdf_row_index": row_index,
            "pdf_headers": headers,
        },
    )


def _default_converter() -> Any:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise PdfParserUnavailable("Docling is not installed; install the 'parsing' extra") from exc
    return DocumentConverter()


def parse_pdf(
    path: str | Path,
    *,
    converter: Any | None = None,
) -> PdfParseResult:
    """Extract German LV table rows from a PDF and normalize them into items."""

    source = Path(path)
    if not source.is_file():
        raise PdfParseError(f"PDF not found: {source}")

    active_converter = converter if converter is not None else _default_converter()
    try:
        conversion = active_converter.convert(source)
    except Exception as exc:
        raise PdfParseError(f"Docling failed to convert {source.name}: {exc}") from exc

    document = getattr(conversion, "document", conversion)
    tables = list(getattr(document, "tables", ()) or ())
    if not tables:
        raise PdfParseError(f"no tables detected in {source.name}")

    warnings: list[str] = []
    if getattr(conversion, "has_errors", False):
        warnings.append(f"Docling reported conversion errors for {source.name}")

    items: list[TenderItem] = []
    seen: set[tuple[str, str]] = set()
    for table_index, table in enumerate(tables):
        try:
            records = _table_records(table, document)
        except Exception as exc:
            warnings.append(f"{source.name} table {table_index + 1}: extraction failed: {exc}")
            continue
        for row_index, record in enumerate(records):
            item = _build_item(
                record,
                source=source,
                table_index=table_index,
                row_index=row_index,
                warnings=warnings,
            )
            if item is None:
                continue
            key = (item.lot or "", item.position_number)
            if key in seen:
                warnings.append(f"duplicate PDF position {item.position_number!r}; skipped")
                continue
            seen.add(key)
            items.append(item)

    if not items:
        raise PdfParseError(f"no LV positions extracted from {source.name}")

    return PdfParseResult(
        items=tuple(items),
        warnings=tuple(warnings),
        table_count=len(tables),
        converter_name=type(active_converter).__name__,
    )
