"""durable admin-triggered worker commands

Revision ID: 0018_worker_commands
Revises: 0017_ingestion_quarantine
Create Date: 2026-11-18 02:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_worker_commands"
down_revision: str | None = "0017_ingestion_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_commands",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("command_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("portal", sa.String(length=64), nullable=True),
        sa.Column("max_pages", sa.Integer(), nullable=True),
        sa.Column("batch_limit", sa.Integer(), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "command_type IN ('scrape', 'ingestion')",
            name="ck_worker_commands_command_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_worker_commands_status_valid",
        ),
        sa.CheckConstraint(
            "max_pages IS NULL OR max_pages BETWEEN 1 AND 5",
            name="ck_worker_commands_max_pages_valid",
        ),
        sa.CheckConstraint(
            "batch_limit IS NULL OR batch_limit BETWEEN 1 AND 50",
            name="ck_worker_commands_batch_limit_valid",
        ),
        sa.CheckConstraint("pid IS NULL OR pid > 0", name="ck_worker_commands_pid_positive"),
    )
    op.create_index("ix_worker_commands_created_at", "worker_commands", ["created_at"])
    op.create_index("ix_worker_commands_status", "worker_commands", ["status"])
    op.create_index(
        "uq_worker_commands_active_type",
        "worker_commands",
        ["command_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_worker_commands_active_type", table_name="worker_commands")
    op.drop_index("ix_worker_commands_status", table_name="worker_commands")
    op.drop_index("ix_worker_commands_created_at", table_name="worker_commands")
    op.drop_table("worker_commands")
