"""integration idempotency keys

Revision ID: 0003_integration_idempotency
Revises: 0002_audit_truncate_guard
Create Date: 2026-01-20 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_integration_idempotency"
down_revision: str | None = "0002_audit_truncate_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_idempotency",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_integration_idempotency_status_valid",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_integration_idempotency_request_hash_format",
        ),
        sa.PrimaryKeyConstraint("key", name="pk_integration_idempotency"),
    )
    op.create_index(
        "ix_integration_idempotency_status",
        "integration_idempotency",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_integration_idempotency_operation",
        "integration_idempotency",
        ["operation"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_integration_idempotency_operation", table_name="integration_idempotency")
    op.drop_index("ix_integration_idempotency_status", table_name="integration_idempotency")
    op.drop_table("integration_idempotency")
