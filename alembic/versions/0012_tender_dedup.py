"""cross-portal tender deduplication fingerprint

Revision ID: 0012_tender_dedup
Revises: 0011_catalog_merge
Create Date: 2026-07-02 00:00:00.000000

"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0012_tender_dedup"
down_revision: str | None = "0011_catalog_merge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION = "v1"
_REPLACEMENTS = {
    "&": " und ",
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
}
_NON_WORD = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def _normalize(value: str) -> str:
    lowered = value.casefold()
    for source, replacement in _REPLACEMENTS.items():
        lowered = lowered.replace(source, replacement)
    without_marks = "".join(
        char for char in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(char)
    )
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", without_marks)).strip()


def _dedup_hash(title: str, client_name: str, deadline: datetime) -> str:
    day = deadline.astimezone(UTC).date().isoformat() if deadline.tzinfo else deadline.date().isoformat()
    parts = (_VERSION, _normalize(title), _normalize(client_name), day)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column(
        "tenders",
        sa.Column("dedup_hash", sa.String(length=64), nullable=True),
    )

    bind = op.get_bind()
    if not context.is_offline_mode():
        rows = bind.execute(
            sa.text("SELECT id, title, client_name, submission_deadline FROM tenders ORDER BY created_at, id")
        ).mappings()
        seen: set[str] = set()
        for row in rows.all():
            digest = _dedup_hash(
                str(row["title"]),
                str(row["client_name"]),
                row["submission_deadline"],
            )
            if digest in seen:
                # Historical duplicates keep a null fingerprint; the first row
                # remains the canonical target for future syndication checks.
                continue
            seen.add(digest)
            bind.execute(
                sa.text("UPDATE tenders SET dedup_hash = :digest WHERE id = :id"),
                {"digest": digest, "id": row["id"]},
            )

    op.create_unique_constraint("uq_tenders_dedup_hash", "tenders", ["dedup_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_tenders_dedup_hash", "tenders", type_="unique")
    op.drop_column("tenders", "dedup_hash")
