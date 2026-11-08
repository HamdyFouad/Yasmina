"""durable ERPNext quotation sync outbox

Revision ID: 0013_quotation_sync
Revises: 0012_tender_dedup
Create Date: 2026-07-03 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013_quotation_sync"
down_revision: str | None = "0012_tender_dedup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quotation_sync_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_number", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("6"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("quotation_name", sa.String(length=140), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed', 'skipped')",
            name="ck_quotation_sync_jobs_status_valid",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_quotation_sync_jobs_attempts_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["customer_offers.id"],
            name="fk_quotation_sync_jobs_offer_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quotation_sync_jobs"),
        sa.UniqueConstraint("offer_id", name="uq_quotation_sync_jobs_offer_id"),
    )
    op.create_index(
        "ix_quotation_sync_jobs_offer_id",
        "quotation_sync_jobs",
        ["offer_id"],
        unique=False,
    )
    op.create_index(
        "ix_quotation_sync_jobs_status_available",
        "quotation_sync_jobs",
        ["status", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_quotation_sync_jobs_status_available", table_name="quotation_sync_jobs")
    op.drop_index("ix_quotation_sync_jobs_offer_id", table_name="quotation_sync_jobs")
    op.drop_table("quotation_sync_jobs")
