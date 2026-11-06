"""GAEB DA XML parser/writer adapter built on pyGAEB.

Reads tender/bid documents (``.x83``, ``.d83``) into the strict Yasmina
``Tender`` schema and writes approved customer offers back as ``.x84`` bids.
pyGAEB is an optional dependency and is imported lazily so the rest of the
application can run without the parsing extra installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from src.models.enums import (
    Currency,
    CustomerOfferStatus,
    GloveSize,
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    TenderSourceFormat,
    UnitOfMeasure,
)
from src.models.schemas import CustomerOffer, Tender, TenderItem, q2
from src.parsers.normalizers import (
    normalize_color,
    normalize_glove_size,
    normalize_material,
    normalize_unit_of_measure,
)
from src.services.approval_service import ApprovalService

__all__ = [
    "GaebExportError",
    "GaebParseError",
    "GaebParseResult",
    "parse_gaeb",
    "write_x84",
]

APPROVED_EXPORT_STATUSES = frozenset(
    {
        CustomerOfferStatus.APPROVED,
        CustomerOfferStatus.SENT,
        CustomerOfferStatus.ACCEPTED,
    }
)

_SOURCE_FORMATS = {
    ".x83": TenderSourceFormat.GAEB_X83,
    ".d83": TenderSourceFormat.GAEB_D83,
    ".x84": TenderSourceFormat.GAEB_X84,
}

_PROCEDURE_KEYWORDS: tuple[tuple[str, ProcurementProcedure], ...] = (
    ("nicht offen", ProcurementProcedure.RESTRICTED_PROCEDURE),
    ("beschränkt", ProcurementProcedure.RESTRICTED_PROCEDURE),
    ("beschraenkt", ProcurementProcedure.RESTRICTED_PROCEDURE),
    ("restricted", ProcurementProcedure.RESTRICTED_PROCEDURE),
    ("verhand", ProcurementProcedure.NEGOTIATED_PROCEDURE),
    ("negotiat", ProcurementProcedure.NEGOTIATED_PROCEDURE),
    ("dialog", ProcurementProcedure.COMPETITIVE_DIALOGUE),
    ("direkt", ProcurementProcedure.DIRECT_AWARD),
    ("freihändig", ProcurementProcedure.DIRECT_AWARD),
    ("freihaendig", ProcurementProcedure.DIRECT_AWARD),
    ("direct", ProcurementProcedure.DIRECT_AWARD),
    ("offen", ProcurementProcedure.OPEN_PROCEDURE),
    ("open", ProcurementProcedure.OPEN_PROCEDURE),
)

_GLOVE_SIZE_RE = re.compile(
    r"\b(?:gr\.?|größe|groesse|size)\s*(xxl|xl|xs|m|l|s|\d{1,2}(?:[.,]\d)?)\b",
    re.IGNORECASE,
)
_THICKNESS_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>µm|um|mikron|micron|mm)\b",
    re.IGNORECASE,
)
_DIMENSIONS_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:mm|cm)\b", re.IGNORECASE)
_PACK_AFTER_RE = re.compile(
    r"\b(?:à|je|inhalt|packung|pack|karton)\s*[:\-]?\s*(?P<count>\d{1,3}(?:[.,]\d{3})*|\d+)\b",
    re.IGNORECASE,
)
_PACK_BEFORE_RE = re.compile(
    r"(?P<count>\d{1,3}(?:[.,]\d{3})*|\d+)\s*(?:stk\.?|stück|stueck|st\.?)\s*(?:je|pro|/)\s*(?:karton|pack|packung)\b",
    re.IGNORECASE,
)


class GaebParseError(ValueError):
    """Raised when a GAEB file cannot be converted into a valid tender."""


class GaebExportError(ValueError):
    """Raised when an X84 bid cannot be written (approval, prices, XSD)."""


@dataclass(frozen=True)
class GaebParseResult:
    """Parsed tender plus the pyGAEB document needed for later X84 writing."""

    tender: Tender
    document: Any
    warnings: tuple[str, ...] = ()
    source_version: str | None = None
    exchange_phase: str | None = None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _source_format(path: Path) -> TenderSourceFormat:
    suffix = path.suffix.lower()
    try:
        return _SOURCE_FORMATS[suffix]
    except KeyError as exc:
        raise GaebParseError(f"unsupported GAEB file extension {suffix!r}") from exc


def _map_procedure(raw: str | None, warnings: list[str]) -> ProcurementProcedure:
    if raw:
        lowered = raw.lower()
        for keyword, procedure in _PROCEDURE_KEYWORDS:
            if keyword in lowered:
                return procedure
        warnings.append(f"unknown procurement type {raw!r}; defaulting to OPEN_PROCEDURE")
    return ProcurementProcedure.OPEN_PROCEDURE


def _map_currency(raw: str | None, warnings: list[str]) -> Currency:
    if raw:
        try:
            return Currency(raw.strip().upper())
        except ValueError:
            warnings.append(f"unsupported currency {raw!r}; defaulting to EUR")
    return Currency.EUR


def _walk_category(category: Any, lot_label: str, result: dict[int, str]) -> None:
    for item in getattr(category, "items", ()):
        result[id(item)] = lot_label
    for subcategory in getattr(category, "subcategories", ()):
        _walk_category(subcategory, lot_label, result)


def _lot_labels(document: Any) -> dict[int, str]:
    result: dict[int, str] = {}
    award = getattr(document, "award", None)
    boq = getattr(award, "boq", None)
    for lot in getattr(boq, "lots", ()) or ():
        label = getattr(lot, "label", None)
        if not label or str(label).strip().lower() == "default":
            label = f"Los {getattr(lot, 'rno', '')}".strip()
        body = getattr(lot, "body", None)
        for category in getattr(body, "categories", ()) or ():
            _walk_category(category, str(label), result)
    return result


def _extract_glove_size(text: str) -> GloveSize:
    match = _GLOVE_SIZE_RE.search(text)
    if match is None:
        return GloveSize.NOT_APPLICABLE
    return normalize_glove_size(match.group(1))


def _extract_thickness(text: str) -> Decimal | None:
    match = _THICKNESS_RE.search(text)
    if match is None:
        return None
    value = _as_decimal(match.group("value"))
    if value is None:
        return None
    if match.group("unit").lower() == "mm":
        value *= Decimal(1000)
    if value < 1 or value > 2000:
        return None
    return value.quantize(Decimal("0.01"))


def _extract_pack_size(text: str) -> int | None:
    match = _PACK_AFTER_RE.search(text) or _PACK_BEFORE_RE.search(text)
    if match is None:
        return None
    value = _as_decimal(match.group("count"))
    if value is None or value <= 0 or value != value.to_integral_value():
        return None
    return int(value)


def _extract_dimensions(text: str) -> str | None:
    match = _DIMENSIONS_RE.search(text)
    return match.group(0).replace("×", "x") if match else None


def _build_item(
    gaeb_item: Any,
    tender_id: UUID,
    *,
    lot_labels: dict[int, str],
    alternative_allowed: bool,
    source_file: str,
    warnings: list[str],
) -> TenderItem | None:
    quantity = _as_decimal(getattr(gaeb_item, "qty", None))
    if quantity is None or quantity <= 0:
        warnings.append(
            f"skipped position {getattr(gaeb_item, 'full_oz', '?')}: missing or non-positive quantity"
        )
        return None

    short_text = str(getattr(gaeb_item, "short_text", "") or "").strip()
    if not short_text:
        warnings.append(f"skipped position {getattr(gaeb_item, 'full_oz', '?')}: empty short text")
        return None

    long_text_obj = getattr(gaeb_item, "long_text", None)
    long_text = str(getattr(long_text_obj, "plain_text", "") or "").strip() if long_text_obj else None
    combined = f"{short_text} {long_text or ''}"
    unit = normalize_unit_of_measure(getattr(gaeb_item, "unit", None))
    if unit is UnitOfMeasure.NOT_APPLICABLE:
        warnings.append(
            f"position {getattr(gaeb_item, 'full_oz', '?')}: unknown unit {getattr(gaeb_item, 'unit', None)!r}"
        )

    material: MaterialType | None = normalize_material(combined)
    color: StandardizedColor | None = normalize_color(combined)
    if material is MaterialType.NOT_APPLICABLE:
        material = None
    if color is StandardizedColor.UNKNOWN:
        color = None

    position = str(getattr(gaeb_item, "full_oz", None) or getattr(gaeb_item, "oz", "")).strip()
    if not position:
        warnings.append("skipped GAEB position without ordinal number")
        return None

    lot = lot_labels.get(id(gaeb_item)) or getattr(gaeb_item, "lot_label", None)
    item_type = getattr(gaeb_item, "item_type", None)
    item_type_value = getattr(item_type, "value", str(item_type)) if item_type is not None else None

    return TenderItem(
        tender_id=tender_id,
        position_number=position[:64],
        lot=str(lot)[:64] if lot else None,
        short_text=short_text[:512],
        long_text=long_text[:8000] if long_text else None,
        quantity=quantity,
        unit=unit,
        glove_size=_extract_glove_size(combined),
        material=material,
        color=color,
        thickness_um=_extract_thickness(combined),
        dimensions=_extract_dimensions(combined),
        pack_size=_extract_pack_size(combined),
        cpv_code=None,
        is_alternative_allowed=alternative_allowed,
        source_reference=f"{source_file}#{getattr(gaeb_item, 'oz', position)}",
        attributes={
            "gaeb_oz": getattr(gaeb_item, "oz", None),
            "gaeb_full_oz": getattr(gaeb_item, "full_oz", None),
            "gaeb_item_type": item_type_value,
            "gaeb_unit_raw": getattr(gaeb_item, "unit", None),
            "gaeb_hierarchy_path": list(getattr(gaeb_item, "hierarchy_path", ()) or ()),
        },
    )


def parse_gaeb(path: str | Path, *, vat_rate: Decimal = Decimal("0.19")) -> GaebParseResult:
    """Parse a GAEB ``.x83``/``.d83``/``.x84`` file into a strict :class:`Tender`."""

    try:
        from pygaeb import GAEBParser
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise GaebParseError("pyGAEB is not installed; install the 'parsing' extra") from exc

    source = Path(path)
    source_format = _source_format(source)
    if not source.is_file():
        raise GaebParseError(f"GAEB file not found: {source}")

    try:
        document = GAEBParser.parse(source)
    except Exception as exc:
        raise GaebParseError(f"pyGAEB could not parse {source.name}: {exc}") from exc

    award = getattr(document, "award", None)
    if award is None or getattr(award, "boq", None) is None:
        raise GaebParseError(f"{source.name} is not a procurement tender (no award/BoQ)")

    warnings: list[str] = []
    tender_id = uuid4()
    lot_labels = _lot_labels(document)
    alternative_allowed = bool(getattr(award, "alter_bid_perm", False))

    items: list[TenderItem] = []
    seen: set[tuple[str, str]] = set()
    for gaeb_item in document.iter_items():
        built = _build_item(
            gaeb_item,
            tender_id,
            lot_labels=lot_labels,
            alternative_allowed=alternative_allowed,
            source_file=source.name,
            warnings=warnings,
        )
        if built is None:
            continue
        key = (built.lot or "", built.position_number)
        if key in seen:
            warnings.append(f"skipped duplicate position {built.position_number!r} in lot {built.lot!r}")
            continue
        seen.add(key)
        items.append(built)

    if not items:
        raise GaebParseError(f"no usable tender positions found in {source.name}")

    deadline = _as_aware(getattr(award, "open_date", None) or getattr(award, "date", None))
    if deadline is None:
        deadline = datetime.now(UTC) + timedelta(days=30)
        warnings.append("no submission/open date in GAEB file; using now + 30 days")

    publication_raw = _as_aware(getattr(award, "date", None))
    publication_date: date | None = publication_raw.date() if publication_raw else None

    boq_info = getattr(getattr(award, "boq", None), "boq_info", None)
    title = (
        getattr(award, "project_name", None)
        or getattr(boq_info, "name", None)
        or getattr(boq_info, "lbl_boq", None)
        or f"GAEB tender {source.stem}"
    )
    client = getattr(award, "client", None)
    if not client:
        owner = getattr(award, "owner_address", None)
        client = getattr(owner, "name", None) if owner else None
    if not client:
        client = "Unbekannter Auftraggeber"
        warnings.append("no client name in GAEB file; using placeholder")

    tender_number = (
        getattr(award, "award_no", None)
        or getattr(award, "boq_id", None)
        or getattr(award, "project_no", None)
        or source.stem
    )

    try:
        tender = Tender(
            id=tender_id,
            tender_number=str(tender_number)[:64],
            title=str(title)[:512],
            description=(str(getattr(award, "description", "") or "")[:8000] or None),
            client_name=str(client)[:256],
            procedure=_map_procedure(getattr(award, "procurement_type", None), warnings),
            source_format=source_format,
            source_reference=source.name,
            publication_date=publication_date,
            submission_deadline=deadline,
            currency=_map_currency(getattr(award, "currency", None), warnings),
            vat_rate=vat_rate,
            items=items,
            metadata={
                "gaeb_source_version": str(getattr(document, "source_version", "")),
                "gaeb_exchange_phase": str(getattr(document, "exchange_phase", "")),
                "gaeb_project_no": getattr(award, "project_no", None),
                "gaeb_boq_id": getattr(award, "boq_id", None),
                "gaeb_procurement_type": getattr(award, "procurement_type", None),
                "source_file": source.name,
            },
        )
    except ValidationError as exc:
        raise GaebParseError(f"parsed GAEB data failed schema validation: {exc}") from exc

    return GaebParseResult(
        tender=tender,
        document=document,
        warnings=tuple(warnings),
        source_version=str(getattr(document, "source_version", None)),
        exchange_phase=str(getattr(document, "exchange_phase", None)),
    )


def _ensure_exportable(offer: CustomerOffer) -> None:
    if offer.status not in APPROVED_EXPORT_STATUSES:
        raise GaebExportError(f"offer {offer.offer_number} is not approved (status={offer.status})")
    if not offer.ed25519_approval_signature or not offer.approval_payload_hash:
        raise GaebExportError(f"offer {offer.offer_number} has no Ed25519 approval signature")


def write_x84(
    document: Any,
    offer: CustomerOffer,
    output: str | Path,
    *,
    prog_system: str = "Project Yasmina",
    prog_name: str = "Yasmina",
    xsd_dir: str | Path | None = None,
    approval_service: ApprovalService,
) -> tuple[str, ...]:
    """Write an approved offer as a GAEB ``.x84`` bid.

    The pyGAEB document from :func:`parse_gaeb` must be reused so the bid keeps
    the tender's positions and identifiers. Unit prices are copied from the
    offer lines by position number. ``approval_service`` must verify the
    offer's Ed25519 signature against the configured public key.
    """

    try:
        from pygaeb import ExchangePhase, GAEBWriter
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise GaebExportError("pyGAEB is not installed; install the 'parsing' extra") from exc

    output_path = Path(output)
    if output_path.suffix.lower() != ".x84":
        raise GaebExportError(f"GAEB bid output must use the .x84 extension, got {output_path.suffix!r}")

    _ensure_exportable(offer)
    if not approval_service.verify(offer):
        raise GaebExportError("offer Ed25519 signature does not verify against the configured public key")

    prices: dict[str, Decimal] = {}
    for line in offer.lines:
        prices[line.position_number] = line.unit_price_net

    matched = 0
    for item in document.iter_items():
        position = str(getattr(item, "full_oz", None) or getattr(item, "oz", "")).strip()
        price = prices.get(position)
        if price is None:
            price = prices.get(str(getattr(item, "oz", "")).strip())
        if price is None:
            continue
        item.unit_price = price
        quantity = _as_decimal(getattr(item, "qty", None))
        if quantity is not None and quantity > 0:
            item.total_price = q2(quantity * price)
        matched += 1

    if matched == 0:
        raise GaebExportError("no offer line matched a GAEB position; refusing to write an empty bid")

    if xsd_dir is not None:
        result = GAEBWriter.validate_against_xsd(
            document,
            phase=ExchangePhase.X84,
            xsd_dir=xsd_dir,
            prog_system=prog_system,
            prog_name=prog_name,
        )
        if result is not None and not result.valid:
            details = "; ".join(f"line {error.line}: {error.message}" for error in result.errors[:5])
            raise GaebExportError(f"X84 document failed XSD validation: {details}")

    warnings = GAEBWriter.write(
        document,
        output_path,
        phase=ExchangePhase.X84,
        prog_system=prog_system,
        prog_name=prog_name,
    )
    return tuple(warnings)
