"""offer persistence and LangGraph checkpoints

Revision ID: 0004_offer_workflow_persistence
Revises: 0003_integration_idempotency
Create Date: 2026-02-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_offer_workflow_persistence"
down_revision: str | None = "0003_integration_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customer_offers", sa.Column("owner", sa.String(length=128), nullable=True))
    op.add_column(
        "customer_offers",
        sa.Column(
            "invoice_artifacts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index("ix_customer_offers_owner", "customer_offers", ["owner"], unique=False)

    op.create_table(
        "workflow_checkpoints",
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("checkpoint_type", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_blob", sa.LargeBinary(), nullable=False),
        sa.Column("metadata_type", sa.String(length=64), nullable=False),
        sa.Column("metadata_blob", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id", name="pk_workflow_checkpoints"),
    )
    op.create_index(
        "ix_workflow_checkpoints_thread",
        "workflow_checkpoints",
        ["thread_id", "checkpoint_ns"],
        unique=False,
    )

    op.create_table(
        "workflow_checkpoint_blobs",
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("value_type", sa.String(length=64), nullable=False),
        sa.Column("value_blob", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "channel",
            "version",
            name="pk_workflow_checkpoint_blobs",
        ),
    )

    op.create_table(
        "workflow_checkpoint_writes",
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=255), nullable=False),
        sa.Column("value_type", sa.String(length=64), nullable=False),
        sa.Column("value_blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.String(length=1024), server_default=sa.text("''"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
            name="pk_workflow_checkpoint_writes",
        ),
    )


def downgrade() -> None:
    op.drop_table("workflow_checkpoint_writes")
    op.drop_table("workflow_checkpoint_blobs")
    op.drop_index("ix_workflow_checkpoints_thread", table_name="workflow_checkpoints")
    op.drop_table("workflow_checkpoints")
    op.drop_index("ix_customer_offers_owner", table_name="customer_offers")
    op.drop_column("customer_offers", "invoice_artifacts")
    op.drop_column("customer_offers", "owner")
