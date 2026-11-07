"""catalogue merge provenance and embedding model tracking

Revision ID: 0011_catalog_merge
Revises: 0010_account_lifecycle
Create Date: 2026-07-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011_catalog_merge"
down_revision: str | None = "0010_account_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_articles",
        sa.Column(
            "manual_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "catalog_articles",
        sa.Column("last_imported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "catalog_articles",
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "manual_overrides_array",
        "catalog_articles",
        "jsonb_typeof(manual_overrides) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_catalog_articles_manual_overrides_array",
        "catalog_articles",
        type_="check",
    )
    op.drop_column("catalog_articles", "embedding_model")
    op.drop_column("catalog_articles", "last_imported_at")
    op.drop_column("catalog_articles", "manual_overrides")
