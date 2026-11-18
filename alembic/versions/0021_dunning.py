"""automated dunning state on invoices

Revision ID: 0021_dunning
Revises: 0020_procurement
Create Date: 2026-11-18 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_dunning"
down_revision: str | None = "0020_procurement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("dunning_level", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "invoices",
        sa.Column("dunning_last_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "dunning_level_range",
        "invoices",
        "dunning_level >= 0 AND dunning_level <= 2",
    )
    op.create_index(
        "ix_invoices_dunning_due",
        "invoices",
        ["status", "due_date", "dunning_level"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_dunning_due", table_name="invoices")
    op.drop_constraint("ck_invoices_dunning_level_range", "invoices", type_="check")
    op.drop_column("invoices", "dunning_last_sent_at")
    op.drop_column("invoices", "dunning_level")
