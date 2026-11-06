"""worker run history and metrics

Revision ID: 0006_worker_runs
Revises: 0005_ingestion_outbox
Create Date: 2026-03-15 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_worker_runs"
down_revision: str | None = "0005_ingestion_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_type", sa.String(length=16), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'running'"), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("items_processed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("errors_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "run_type IN ('scrape', 'ingestion')",
            name="ck_worker_runs_run_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped', 'cancelled')",
            name="ck_worker_runs_status_valid",
        ),
        sa.CheckConstraint("items_processed >= 0", name="ck_worker_runs_items_non_negative"),
        sa.CheckConstraint("errors_count >= 0", name="ck_worker_runs_errors_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_worker_runs"),
    )
    op.create_index("ix_worker_runs_started_at", "worker_runs", ["started_at"], unique=False)
    op.create_index("ix_worker_runs_type_status", "worker_runs", ["run_type", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_worker_runs_type_status", table_name="worker_runs")
    op.drop_index("ix_worker_runs_started_at", table_name="worker_runs")
    op.drop_table("worker_runs")
