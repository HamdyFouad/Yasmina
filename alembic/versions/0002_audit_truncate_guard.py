"""block TRUNCATE on audit_logs

The initial schema blocked UPDATE and DELETE via a row-level trigger. TRUNCATE
is a statement-level operation and bypassed that guard, so a second trigger is
required to keep the audit chain append-only.

Revision ID: 0002_audit_truncate_guard
Revises: 0001_initial_schema
Create Date: 2026-01-01 00:00:01.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_audit_truncate_guard"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION yasmina_reject_audit_log_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON audit_logs")
