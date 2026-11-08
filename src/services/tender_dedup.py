"""Cross-portal tender identity and deduplication.

Local portals frequently syndicate the same notice to DTVP/oeffentlichevergabe
or TED. The portal-specific tender number cannot detect that, so a conservative
fingerprint over normalized title, client and submission date is stored in
``tenders.dedup_hash`` and checked before inserting a new row.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from typing import Protocol

__all__ = [
    "TENDER_DEDUP_VERSION",
    "normalize_dedup_text",
    "tender_dedup_hash",
    "tender_fingerprint",
]


class TenderLike(Protocol):
    title: str
    client_name: str
    submission_deadline: datetime


# Bump only when the normalization rules change; the migration backfill uses the
# same version marker in its helper.
TENDER_DEDUP_VERSION = "v1"
_NON_WORD = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")
_REPLACEMENTS = {
    "&": " und ",
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
}


def normalize_dedup_text(value: str) -> str:
    """Normalize case, umlauts, punctuation and whitespace for comparison."""

    lowered = value.casefold()
    for source, replacement in _REPLACEMENTS.items():
        lowered = lowered.replace(source, replacement)
    without_marks = "".join(
        char for char in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(char)
    )
    words = _NON_WORD.sub(" ", without_marks)
    return _WHITESPACE.sub(" ", words).strip()


def _deadline_date(value: datetime) -> str:
    if value.tzinfo is None:
        return value.date().isoformat()
    return value.astimezone(UTC).date().isoformat()


def tender_dedup_hash(
    title: str,
    client_name: str,
    submission_deadline: datetime,
) -> str:
    """Return the stable fingerprint used by the cross-portal dedupe check."""

    parts = (
        TENDER_DEDUP_VERSION,
        normalize_dedup_text(title),
        normalize_dedup_text(client_name),
        _deadline_date(submission_deadline),
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def tender_fingerprint(tender: TenderLike) -> str:
    """Convenience wrapper for a schema/model object with the three fields."""

    return tender_dedup_hash(
        tender.title,
        tender.client_name,
        tender.submission_deadline,
    )
