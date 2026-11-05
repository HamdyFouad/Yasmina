"""Shared models and CPV/date helpers for portal scrapers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.models.enums import (
    ProcurementProcedure,
    TenderSourceFormat,
    TenderStatus,
)
from src.models.schemas import Tender

__all__ = [
    "HYGIENE_CPV_CODES",
    "DiscoveredTender",
    "PortalAttachment",
    "cpv_base",
    "extract_cpv_codes",
    "filter_hygiene_cpv",
    "is_hygiene_cpv",
    "normalize_cpv",
    "parse_portal_date",
    "parse_portal_datetime",
]

HYGIENE_CPV_CODES: tuple[str, ...] = (
    "33760000-5",  # Toilettenpapier, Taschentücher, Handtücher, Servietten
    "19520000-7",  # Kunststofferzeugnisse
    "33141420-0",  # Chirurgische Handschuhe
    "39830000-9",  # Reinigungsmittel
)

_HYGIENE_BASES = frozenset(code.split("-", 1)[0] for code in HYGIENE_CPV_CODES)
_CPV_RE = re.compile(r"(?<!\d)(\d{8})(?:-(\d))?(?!\d)")
_BERLIN = ZoneInfo("Europe/Berlin")
_MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "märz": 3,
    "maerz": 3,
    "mrz": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}
_EN_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_ISO_RE = re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2}))?")
_EU_RE = re.compile(
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
    r"(?:\s*[,um ]*\s*(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
_LONG_RE = re.compile(
    r"(?P<day>\d{1,2})\.?\s+(?P<month>[A-Za-zÄÖÜäöüß]+)\s+(?P<year>\d{4})"
    r"(?:\s*[,um ]*\s*(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
_EN_RE = re.compile(
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})[,\s]+(?P<year>\d{4})"
    r"(?:,?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)?)?",
    re.IGNORECASE,
)


def normalize_cpv(value: str) -> str | None:
    """Return the canonical ``########-C`` form when present in ``value``."""

    match = _CPV_RE.search(value)
    if match is None:
        return None
    base, check = match.groups()
    return f"{base}-{check}" if check else base


def cpv_base(value: str) -> str | None:
    normalized = normalize_cpv(value)
    return normalized.split("-", 1)[0] if normalized else None


def is_hygiene_cpv(value: str) -> bool:
    """Strict match against the four approved CPV code families."""

    base = cpv_base(value)
    return base is not None and base in _HYGIENE_BASES


def filter_hygiene_cpv(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Normalize, deduplicate and retain only approved hygiene/cleaning CPVs."""

    result: list[str] = []
    for value in values:
        normalized = normalize_cpv(value)
        if normalized is not None and is_hygiene_cpv(normalized) and normalized not in result:
            result.append(normalized)
    return tuple(result)


def extract_cpv_codes(text: str) -> tuple[str, ...]:
    """Extract every CPV-shaped code from free text (all categories)."""

    codes: list[str] = []
    for match in _CPV_RE.finditer(text):
        base, check = match.groups()
        code = f"{base}-{check}" if check else base
        if code not in codes:
            codes.append(code)
    return tuple(codes)


def _to_berlin(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_BERLIN)
    return value.astimezone(_BERLIN)


def _combine(year: int, month: int, day: int, hour: int | None, minute: int | None) -> datetime | None:
    try:
        if hour is None:
            return datetime(year, month, day, 0, 0, tzinfo=_BERLIN)
        return datetime(year, month, day, hour, minute or 0, tzinfo=_BERLIN)
    except ValueError:
        return None


def parse_portal_datetime(text: str, *, end_of_day: bool = True) -> datetime | None:
    """Parse the common German/ISO/English portal deadline formats."""

    if not text:
        return None
    normalized = " ".join(
        text.replace("\xa0", " ")
        .replace("Uhr", " ")
        .replace(",", " ")
        .split()
    )

    iso = _ISO_RE.search(normalized)
    if iso is not None:
        hour = int(iso.group("hour")) if iso.group("hour") else None
        minute = int(iso.group("minute")) if iso.group("minute") else None
        parsed = _combine(
            int(iso.group("year")),
            int(iso.group("month")),
            int(iso.group("day")),
            hour,
            minute,
        )
        if parsed is not None:
            return parsed if hour is not None or not end_of_day else parsed.replace(hour=23, minute=59)

    eu = _EU_RE.search(normalized)
    if eu is not None:
        hour = int(eu.group("hour")) if eu.group("hour") else None
        minute = int(eu.group("minute")) if eu.group("minute") else None
        parsed = _combine(
            int(eu.group("year")),
            int(eu.group("month")),
            int(eu.group("day")),
            hour,
            minute,
        )
        if parsed is not None:
            return parsed if hour is not None or not end_of_day else parsed.replace(hour=23, minute=59)

    long_match = _LONG_RE.search(normalized)
    if long_match is not None:
        month_name = long_match.group("month").casefold()
        month = _MONTHS.get(month_name) or _EN_MONTHS.get(month_name)
        if month is not None:
            hour = int(long_match.group("hour")) if long_match.group("hour") else None
            minute = int(long_match.group("minute")) if long_match.group("minute") else None
            parsed = _combine(
                int(long_match.group("year")),
                month,
                int(long_match.group("day")),
                hour,
                minute,
            )
            if parsed is not None:
                return parsed if hour is not None or not end_of_day else parsed.replace(hour=23, minute=59)

    en_match = _EN_RE.search(normalized)
    if en_match is not None:
        month_name = en_match.group("month").casefold()
        month = _EN_MONTHS.get(month_name)
        if month is not None:
            hour = int(en_match.group("hour")) if en_match.group("hour") else None
            minute = int(en_match.group("minute")) if en_match.group("minute") else None
            ampm = (en_match.group("ampm") or "").upper()
            if hour is not None and ampm == "PM" and hour != 12:
                hour += 12
            if hour is not None and ampm == "AM" and hour == 12:
                hour = 0
            parsed = _combine(
                int(en_match.group("year")),
                month,
                int(en_match.group("day")),
                hour,
                minute,
            )
            if parsed is not None:
                return parsed if hour is not None or not end_of_day else parsed.replace(hour=23, minute=59)

    return None


def parse_portal_date(text: str) -> date | None:
    parsed = parse_portal_datetime(text, end_of_day=False)
    return parsed.date() if parsed is not None else None


def _safe_number(portal_code: str, portal_id: str, detail_url: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", portal_id).strip("-")
    if not raw:
        raw = hashlib.sha256(detail_url.encode("utf-8")).hexdigest()[:12]
    raw = raw[:52]
    return f"{portal_code.upper()[:8]}-{raw}"[:64]


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _to_berlin(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True, slots=True)
class PortalAttachment:
    """A downloaded and archived portal attachment."""

    url: str
    filename: str
    object_name: str
    content_type: str
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "filename": self.filename,
            "object_name": self.object_name,
            "content_type": self.content_type,
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class DiscoveredTender:
    """Normalized tender metadata discovered on a procurement portal."""

    portal: str
    portal_code: str
    portal_id: str
    title: str
    client_name: str
    detail_url: str
    submission_deadline: datetime
    cpv_codes: tuple[str, ...]
    publication_date: date | None = None
    procedure: ProcurementProcedure = ProcurementProcedure.OPEN_PROCEDURE
    description: str | None = None
    raw_object: str | None = None
    raw_objects: tuple[str, ...] = ()
    attachments: tuple[PortalAttachment, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.submission_deadline.tzinfo is None:
            raise ValueError("submission_deadline must be timezone-aware")
        matched = filter_hygiene_cpv(self.cpv_codes)
        if not matched:
            raise ValueError("discovered tender has no approved hygiene/cleaning CPV code")
        if not self.title.strip():
            raise ValueError("discovered tender has no title")
        if not self.detail_url.startswith(("http://", "https://")):
            raise ValueError("detail_url must be absolute")

    def to_tender(self) -> Tender:
        """Map portal metadata onto the strict ``Tender`` schema."""

        now = datetime.now(timezone.utc)
        metadata = {
            **_jsonable(dict(self.metadata)),
            "portal": self.portal,
            "portal_code": self.portal_code,
            "portal_id": self.portal_id,
            "detail_url": self.detail_url,
            "raw_object": self.raw_object,
            "raw_objects": list(self.raw_objects),
            "attachments": [attachment.as_dict() for attachment in self.attachments],
            "attachment_objects": [attachment.object_name for attachment in self.attachments],
        }
        return Tender(
            tender_number=_safe_number(self.portal_code, self.portal_id, self.detail_url),
            title=self.title.strip()[:512],
            description=self.description[:8000] if self.description else None,
            client_name=self.client_name.strip()[:256] or "Unbekannter Auftraggeber",
            procedure=self.procedure,
            source_format=TenderSourceFormat.PORTAL,
            source_reference=self.detail_url[:512],
            cpv_codes=list(filter_hygiene_cpv(self.cpv_codes)),
            publication_date=self.publication_date,
            submission_deadline=self.submission_deadline.astimezone(timezone.utc),
            status=TenderStatus.PUBLISHED,
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )
