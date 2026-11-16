"""invoice email delivery event

Revision ID: 0016_invoice_email_events
Revises: 0015_business_logic_finalization
Create Date: 2026-11-16 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_invoice_email_events"
down_revision: str | None = "0015_business_logic_finalization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE invoice_events DROP CONSTRAINT IF EXISTS ck_invoice_events_event_type_valid"
    )
    op.execute(
        "ALTER TABLE invoice_events ADD CONSTRAINT ck_invoice_events_event_type_valid "
        "CHECK (event_type IN ('ISSUED', 'PAYMENT_RECORDED', 'CREDIT_ISSUED', "
        "'STATUS_CHANGED', 'CANCELLED', 'EMAIL_SENT'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE invoice_events DROP CONSTRAINT IF EXISTS ck_invoice_events_event_type_valid"
    )
    op.execute(
        "ALTER TABLE invoice_events ADD CONSTRAINT ck_invoice_events_event_type_valid "
        "CHECK (event_type IN ('ISSUED', 'PAYMENT_RECORDED', 'CREDIT_ISSUED', "
        "'STATUS_CHANGED', 'CANCELLED'))"
    )
