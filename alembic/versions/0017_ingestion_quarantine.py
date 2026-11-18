"""dedicated ingestion quarantine state

Revision ID: 0017_ingestion_quarantine
Revises: 0016_invoice_email_events
Create Date: 2026-11-18 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_ingestion_quarantine"
down_revision: str | None = "0016_invoice_email_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tender_ingestion_jobs",
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "tender_ingestion_jobs",
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
    )
    # Preserve the former string-prefix contract while moving the state into
    # dedicated columns. The prefix is retained in last_error for existing
    # operator tooling and audit readability.
    op.execute(
        """
        UPDATE tender_ingestion_jobs
        SET quarantined = true,
            quarantine_reason = NULLIF(
                regexp_replace(last_error, '^SECURITY:\\s*', ''),
                ''
            )
        WHERE status = 'failed'
          AND last_error LIKE 'SECURITY:%'
        """
    )
    op.create_check_constraint(
        "quarantine_state_valid",
        "tender_ingestion_jobs",
        "NOT quarantined OR status = 'failed'",
    )
    op.create_index(
        "ix_tender_ingestion_jobs_quarantined",
        "tender_ingestion_jobs",
        ["quarantined"],
        unique=False,
    )
    op.create_index(
        "ix_tender_ingestion_jobs_status_locked",
        "tender_ingestion_jobs",
        ["status", "locked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tender_ingestion_jobs_status_locked",
        table_name="tender_ingestion_jobs",
    )
    op.drop_index(
        "ix_tender_ingestion_jobs_quarantined",
        table_name="tender_ingestion_jobs",
    )
    op.drop_constraint(
        "quarantine_state_valid",
        "tender_ingestion_jobs",
        type_="check",
    )
    op.drop_column("tender_ingestion_jobs", "quarantine_reason")
    op.drop_column("tender_ingestion_jobs", "quarantined")
