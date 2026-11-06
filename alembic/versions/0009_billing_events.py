"""credit notes and append-only invoice lifecycle events

Revision ID: 0009_billing_events
Revises: 0008_identity_security
Create Date: 2026-06-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_billing_events"
down_revision: str | None = "0008_identity_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credit_note_number_sequences",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "year >= 2000 AND year <= 2200",
            name="ck_credit_note_number_sequences_year_range",
        ),
        sa.CheckConstraint(
            "last_value >= 0 AND last_value <= 9999",
            name="ck_credit_note_number_sequences_last_value_range",
        ),
        sa.PrimaryKeyConstraint("year", name="pk_credit_note_number_sequences"),
    )

    op.create_table(
        "credit_notes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_note_number", sa.String(length=32), nullable=False),
        sa.Column("credit_note_year", sa.Integer(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'APPLIED'"), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("total_net", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_gross", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "vat_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "lines",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "artifacts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "credit_note_number ~ '^GS-[0-9]{4}-[0-9]{4}$'",
            name="ck_credit_notes_credit_note_number_format",
        ),
        sa.CheckConstraint(
            "credit_note_year >= 2000 AND credit_note_year <= 2200",
            name="ck_credit_notes_credit_note_year_range",
        ),
        sa.CheckConstraint(
            "sequence_number >= 1 AND sequence_number <= 9999",
            name="ck_credit_notes_sequence_range",
        ),
        sa.CheckConstraint(
            "status IN ('ISSUED', 'APPLIED', 'CANCELLED')",
            name="ck_credit_notes_status_valid",
        ),
        sa.CheckConstraint(
            "total_net >= 0 AND total_vat >= 0 AND total_gross >= 0",
            name="ck_credit_notes_totals_non_negative",
        ),
        sa.CheckConstraint(
            "total_gross = total_net + total_vat",
            name="ck_credit_notes_totals_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_credit_notes_invoice_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credit_notes"),
        sa.UniqueConstraint("credit_note_number", name="uq_credit_notes_credit_note_number"),
        sa.UniqueConstraint(
            "credit_note_year",
            "sequence_number",
            name="uq_credit_note_year_sequence",
        ),
    )
    op.create_index("ix_credit_notes_invoice_id", "credit_notes", ["invoice_id"], unique=False)
    op.create_index(
        "ix_credit_notes_invoice_date",
        "credit_notes",
        ["invoice_id", "issue_date"],
        unique=False,
    )

    op.create_table(
        "invoice_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_invoice_events_sequence_positive"),
        sa.CheckConstraint(
            "event_type IN ('ISSUED', 'PAYMENT_RECORDED', 'CREDIT_ISSUED', 'STATUS_CHANGED', 'CANCELLED')",
            name="ck_invoice_events_event_type_valid",
        ),
        sa.CheckConstraint(
            "prev_hash ~ '^[0-9a-f]{64}$' AND entry_hash ~ '^[0-9a-f]{64}$'",
            name="ck_invoice_events_hash_format",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_invoice_events_amount_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_events_invoice_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_events"),
        sa.UniqueConstraint("invoice_id", "sequence", name="uq_invoice_event_sequence"),
        sa.UniqueConstraint("entry_hash", name="uq_invoice_events_entry_hash"),
    )
    op.create_index("ix_invoice_events_invoice_id", "invoice_events", ["invoice_id"], unique=False)
    op.create_index(
        "ix_invoice_events_invoice_sequence",
        "invoice_events",
        ["invoice_id", "sequence"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION prevent_invoice_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'invoice_events is append-only (GoBD retention)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_events_append_only
        BEFORE UPDATE OR DELETE ON invoice_events
        FOR EACH ROW EXECUTE FUNCTION prevent_invoice_event_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_events_no_truncate
        BEFORE TRUNCATE ON invoice_events
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_invoice_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_invoice_events_no_truncate ON invoice_events")
    op.execute("DROP TRIGGER IF EXISTS trg_invoice_events_append_only ON invoice_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_invoice_event_mutation()")
    op.drop_index("ix_invoice_events_invoice_sequence", table_name="invoice_events")
    op.drop_index("ix_invoice_events_invoice_id", table_name="invoice_events")
    op.drop_table("invoice_events")
    op.drop_index("ix_credit_notes_invoice_date", table_name="credit_notes")
    op.drop_index("ix_credit_notes_invoice_id", table_name="credit_notes")
    op.drop_table("credit_notes")
    op.drop_table("credit_note_number_sequences")
