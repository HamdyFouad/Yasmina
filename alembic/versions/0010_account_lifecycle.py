"""account lifecycle: MFA enrolment, password changes and reset tokens

Revision ID: 0010_account_lifecycle
Revises: 0009_billing_events
Create Date: 2026-06-15 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_account_lifecycle"
down_revision: str | None = "0009_billing_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("users", sa.Column("mfa_secret", sa.String(length=512), nullable=True))
    op.add_column("users", sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("mfa_last_step", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_users_mfa_last_step_non_negative",
        "users",
        "mfa_last_step IS NULL OR mfa_last_step >= 0",
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.String(length=128), nullable=True),
        sa.Column("requested_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_password_reset_tokens_password_reset_token_hash_format",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_reset_tokens_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_password_reset_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_password_reset_tokens_expires",
        "password_reset_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_expires", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_constraint("ck_users_mfa_last_step_non_negative", "users", type_="check")
    op.drop_column("users", "mfa_last_step")
    op.drop_column("users", "mfa_enrolled_at")
    op.drop_column("users", "mfa_secret")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "password_changed_at")
