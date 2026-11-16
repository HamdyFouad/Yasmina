"""single-use MFA recovery codes

Revision ID: 0014_mfa_recovery_codes
Revises: 0013_quotation_sync
Create Date: 2026-07-10 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014_mfa_recovery_codes"
down_revision: str | None = "0013_quotation_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mfa_recovery_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_mfa_recovery_codes_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mfa_recovery_codes"),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_mfa_recovery_codes_user_hash"),
    )
    op.create_index(
        "ix_mfa_recovery_codes_user_unused",
        "mfa_recovery_codes",
        ["user_id", "used_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mfa_recovery_codes_user_unused", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
