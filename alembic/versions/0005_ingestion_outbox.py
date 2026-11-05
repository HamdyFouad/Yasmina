"""portal ingestion outbox and scraper watermarks

Revision ID: 0005_ingestion_outbox
Revises: 0004_offer_workflow_persistence
Create Date: 2026-03-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_ingestion_outbox"
down_revision: str | None = "0004_offer_workflow_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scraper_states",
        sa.Column("portal_name", sa.String(length=64), nullable=False),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("portal_name", name="pk_scraper_states"),
    )

    op.create_table(
        "tender_ingestion_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portal_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_object", sa.String(length=512), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'skipped')",
            name="ck_tender_ingestion_jobs_status_valid",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_tender_ingestion_jobs_attempts_non_negative"),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name="fk_tender_ingestion_jobs_tender_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tender_ingestion_jobs"),
        sa.UniqueConstraint("tender_id", name="uq_tender_ingestion_jobs_tender_id"),
    )
    op.create_index(
        "ix_tender_ingestion_jobs_tender_id",
        "tender_ingestion_jobs",
        ["tender_id"],
        unique=False,
    )
    op.create_index(
        "ix_tender_ingestion_jobs_status_available",
        "tender_ingestion_jobs",
        ["status", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tender_ingestion_jobs_status_available", table_name="tender_ingestion_jobs")
    op.drop_index("ix_tender_ingestion_jobs_tender_id", table_name="tender_ingestion_jobs")
    op.drop_table("tender_ingestion_jobs")
    op.drop_table("scraper_states")
