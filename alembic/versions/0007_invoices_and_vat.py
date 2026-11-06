"""issued invoices, gapless numbering and multi-rate VAT records

Revision ID: 0007_invoices_and_vat
Revises: 0006_worker_runs
Create Date: 2026-04-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_invoices_and_vat"
down_revision: str | None = "0006_worker_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_offer_lines",
        sa.Column("vat_rate", sa.Numeric(6, 4), server_default=sa.text("0.1900"), nullable=False),
    )
    op.execute(
        "UPDATE customer_offer_lines AS line "
        "SET vat_rate = offer.vat_rate "
        "FROM customer_offers AS offer "
        "WHERE offer.id = line.offer_id"
    )
    op.create_check_constraint(
        "ck_customer_offer_lines_vat_rate_range",
        "customer_offer_lines",
        "vat_rate >= 0 AND vat_rate <= 1",
    )

    op.create_table(
        "invoice_number_sequences",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "year >= 2000 AND year <= 2200",
            name="ck_invoice_number_sequences_year_range",
        ),
        sa.CheckConstraint(
            "last_value >= 0 AND last_value <= 9999",
            name="ck_invoice_number_sequences_last_value_range",
        ),
        sa.PrimaryKeyConstraint("year", name="pk_invoice_number_sequences"),
    )

    op.create_table(
        "invoices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.String(length=32), nullable=False),
        sa.Column("invoice_year", sa.Integer(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'ISSUED'"), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'EUR'"), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column("seller_name", sa.String(length=256), nullable=False),
        sa.Column("seller_street", sa.String(length=256), nullable=False),
        sa.Column("seller_city", sa.String(length=128), nullable=False),
        sa.Column("seller_postal_code", sa.String(length=32), nullable=False),
        sa.Column("seller_country_code", sa.String(length=2), server_default=sa.text("'DE'"), nullable=False),
        sa.Column("seller_vat_id", sa.String(length=32), nullable=True),
        sa.Column("seller_email", sa.String(length=320), nullable=True),
        sa.Column("seller_phone", sa.String(length=64), nullable=True),
        sa.Column("seller_contact_name", sa.String(length=256), nullable=True),
        sa.Column("buyer_name", sa.String(length=256), nullable=False),
        sa.Column("buyer_street", sa.String(length=256), nullable=False),
        sa.Column("buyer_city", sa.String(length=128), nullable=False),
        sa.Column("buyer_postal_code", sa.String(length=32), nullable=False),
        sa.Column("buyer_country_code", sa.String(length=2), server_default=sa.text("'DE'"), nullable=False),
        sa.Column("buyer_vat_id", sa.String(length=32), nullable=True),
        sa.Column("buyer_email", sa.String(length=320), nullable=True),
        sa.Column("buyer_reference", sa.String(length=46), nullable=True),
        sa.Column("order_reference", sa.String(length=128), nullable=True),
        sa.Column("payment_terms", sa.String(length=512), nullable=True),
        sa.Column("iban", sa.String(length=34), nullable=True),
        sa.Column("total_net", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_gross", sa.Numeric(18, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(18, 2), server_default=sa.text("0.00"), nullable=False),
        sa.Column("credited_amount", sa.Numeric(18, 2), server_default=sa.text("0.00"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credit_note_number", sa.String(length=64), nullable=True),
        sa.Column(
            "vat_breakdown",
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
            "invoice_number ~ '^INV-[0-9]{4}-[0-9]{4}$'",
            name="ck_invoices_invoice_number_format",
        ),
        sa.CheckConstraint(
            "invoice_year >= 2000 AND invoice_year <= 2200",
            name="ck_invoices_invoice_year_range",
        ),
        sa.CheckConstraint(
            "sequence_number >= 1 AND sequence_number <= 9999",
            name="ck_invoices_sequence_range",
        ),
        sa.CheckConstraint(
            "total_net >= 0 AND total_vat >= 0 AND total_gross >= 0",
            name="ck_invoices_totals_non_negative",
        ),
        sa.CheckConstraint(
            "total_gross = total_net + total_vat",
            name="ck_invoices_totals_consistent",
        ),
        sa.CheckConstraint(
            "paid_amount >= 0 AND credited_amount >= 0",
            name="ck_invoices_payment_amounts_non_negative",
        ),
        sa.CheckConstraint("paid_amount <= total_gross", name="ck_invoices_paid_amount_bounded"),
        sa.CheckConstraint(
            "credited_amount <= total_gross",
            name="ck_invoices_credited_amount_bounded",
        ),
        sa.CheckConstraint(
            "paid_amount + credited_amount <= total_gross",
            name="ck_invoices_payment_plus_credit_bounded",
        ),
        sa.CheckConstraint("issue_date <= due_date", name="ck_invoices_due_date_after_issue"),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["customer_offers.id"],
            name="fk_invoices_offer_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
        sa.UniqueConstraint("offer_id", name="uq_invoices_offer_id"),
        sa.UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
        sa.UniqueConstraint("invoice_year", "sequence_number", name="uq_invoice_year_sequence"),
    )
    op.create_index("ix_invoices_status_due", "invoices", ["status", "due_date"], unique=False)
    op.create_index("ix_invoices_issue_date", "invoices", ["issue_date"], unique=False)

    op.create_table(
        "invoice_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("line_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("line_id >= 1", name="ck_invoice_lines_line_id_positive"),
        sa.CheckConstraint("quantity > 0", name="ck_invoice_lines_quantity_positive"),
        sa.CheckConstraint(
            "unit_price >= 0 AND line_total >= 0",
            name="ck_invoice_lines_prices_non_negative",
        ),
        sa.CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="ck_invoice_lines_vat_rate_range"),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_lines_invoice_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_lines"),
        sa.UniqueConstraint("invoice_id", "line_id", name="uq_invoice_line"),
    )
    op.create_index("ix_invoice_lines_invoice_id", "invoice_lines", ["invoice_id"], unique=False)

    op.create_table(
        "invoice_vat_splits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("taxable_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("rate >= 0 AND rate <= 1", name="ck_invoice_vat_splits_rate_range"),
        sa.CheckConstraint(
            "taxable_amount >= 0 AND tax_amount >= 0",
            name="ck_invoice_vat_splits_amounts_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_vat_splits_invoice_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_vat_splits"),
        sa.UniqueConstraint("invoice_id", "rate", name="uq_invoice_vat_rate"),
    )
    op.create_index("ix_invoice_vat_splits_invoice_id", "invoice_vat_splits", ["invoice_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invoice_vat_splits_invoice_id", table_name="invoice_vat_splits")
    op.drop_table("invoice_vat_splits")
    op.drop_index("ix_invoice_lines_invoice_id", table_name="invoice_lines")
    op.drop_table("invoice_lines")
    op.drop_index("ix_invoices_issue_date", table_name="invoices")
    op.drop_index("ix_invoices_status_due", table_name="invoices")
    op.drop_table("invoices")
    op.drop_table("invoice_number_sequences")
    op.drop_constraint(
        "ck_customer_offer_lines_vat_rate_range",
        "customer_offer_lines",
        type_="check",
    )
    op.drop_column("customer_offer_lines", "vat_rate")
