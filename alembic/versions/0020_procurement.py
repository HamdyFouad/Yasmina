"""purchase orders, supplier invoices and procurement matching

Revision ID: 0020_procurement
Revises: 0019_master_data
Create Date: 2026-11-18 11:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_procurement"
down_revision: str | None = "0019_master_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("supplier_quotes", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("supplier_quotes", sa.Column("accepted_by", sa.String(length=128), nullable=True))
    op.add_column(
        "supplier_quote_lines",
        sa.Column("is_accepted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "supplier_quote_lines",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "purchase_orders",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("po_number", sa.String(length=32), nullable=False),
        sa.Column("supplier_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_name", sa.String(length=256), nullable=False),
        sa.Column("supplier_contact_email", sa.String(length=320), nullable=True),
        sa.Column("tender_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_quote_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_offer_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'EUR'"),
            nullable=False,
        ),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
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
        sa.CheckConstraint("total_net >= 0", name="total_net_non_negative"),
        sa.CheckConstraint(
            "expected_delivery_date IS NULL OR expected_delivery_date >= order_date",
            name="delivery_date_after_order",
        ),
        sa.ForeignKeyConstraint(["customer_offer_id"], ["customer_offers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_quote_id"], ["supplier_quotes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("po_number", name="uq_purchase_orders_po_number"),
        sa.UniqueConstraint("supplier_quote_id", name="uq_purchase_orders_supplier_quote_id"),
    )
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
    op.create_index("ix_purchase_orders_tender_id", "purchase_orders", ["tender_id"])
    op.create_index("ix_purchase_orders_customer_offer_id", "purchase_orders", ["customer_offer_id"])
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])
    op.create_index("ix_purchase_orders_created_at", "purchase_orders", ["created_at"])

    op.create_table(
        "purchase_order_lines",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("purchase_order_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_quote_line_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("tender_item_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("unit_price_net", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "received_quantity",
            sa.Numeric(precision=18, scale=4),
            server_default=sa.text("0.0000"),
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
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint("unit_price_net >= 0", name="unit_price_non_negative"),
        sa.CheckConstraint("total_net >= 0", name="total_non_negative"),
        sa.CheckConstraint(
            "received_quantity >= 0",
            name="received_quantity_non_negative",
        ),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_quote_line_id"], ["supplier_quote_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tender_item_id"], ["tender_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "purchase_order_id",
            "line_number",
            name="uq_purchase_order_line_number",
        ),
    )
    op.create_index(
        "ix_purchase_order_lines_purchase_order_id",
        "purchase_order_lines",
        ["purchase_order_id"],
    )
    op.create_index(
        "ix_purchase_order_lines_supplier_quote_line_id",
        "purchase_order_lines",
        ["supplier_quote_line_id"],
    )

    op.create_table(
        "supplier_invoices",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("purchase_order_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_name", sa.String(length=256), nullable=False),
        sa.Column("po_number", sa.String(length=32), nullable=False),
        sa.Column("invoice_number", sa.String(length=128), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'EUR'"),
            nullable=False,
        ),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "vat_rate",
            sa.Numeric(precision=6, scale=4),
            server_default=sa.text("0.1900"),
            nullable=False,
        ),
        sa.Column("total_vat", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("total_gross", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "exception_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "match_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "variance_net",
            sa.Numeric(precision=18, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("received_by", sa.String(length=128), nullable=False),
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
        sa.CheckConstraint(
            "total_net >= 0 AND total_vat >= 0 AND total_gross >= 0",
            name="totals_non_negative",
        ),
        sa.CheckConstraint(
            "total_gross = total_net + total_vat",
            name="totals_consistent",
        ),
        sa.CheckConstraint(
            "vat_rate >= 0 AND vat_rate <= 1",
            name="vat_rate_range",
        ),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supplier_id",
            "invoice_number",
            name="uq_supplier_invoice_reference",
        ),
    )
    op.create_index("ix_supplier_invoices_purchase_order_id", "supplier_invoices", ["purchase_order_id"])
    op.create_index("ix_supplier_invoices_supplier_id", "supplier_invoices", ["supplier_id"])
    op.create_index("ix_supplier_invoices_status", "supplier_invoices", ["status"])
    op.create_index("ix_supplier_invoices_created_at", "supplier_invoices", ["created_at"])

    op.create_table(
        "supplier_invoice_lines",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("supplier_invoice_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_order_line_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("unit_price_net", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=False),
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
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint(
            "unit_price_net >= 0",
            name="unit_price_non_negative",
        ),
        sa.CheckConstraint("total_net >= 0", name="total_non_negative"),
        sa.ForeignKeyConstraint(["purchase_order_line_id"], ["purchase_order_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_invoice_id"], ["supplier_invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "supplier_invoice_id",
            "purchase_order_line_id",
            name="uq_supplier_invoice_po_line",
        ),
    )
    op.create_index(
        "ix_supplier_invoice_lines_supplier_invoice_id",
        "supplier_invoice_lines",
        ["supplier_invoice_id"],
    )
    op.create_index(
        "ix_supplier_invoice_lines_purchase_order_line_id",
        "supplier_invoice_lines",
        ["purchase_order_line_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supplier_invoice_lines_purchase_order_line_id",
        table_name="supplier_invoice_lines",
    )
    op.drop_index(
        "ix_supplier_invoice_lines_supplier_invoice_id",
        table_name="supplier_invoice_lines",
    )
    op.drop_table("supplier_invoice_lines")
    op.drop_index("ix_supplier_invoices_created_at", table_name="supplier_invoices")
    op.drop_index("ix_supplier_invoices_status", table_name="supplier_invoices")
    op.drop_index("ix_supplier_invoices_supplier_id", table_name="supplier_invoices")
    op.drop_index("ix_supplier_invoices_purchase_order_id", table_name="supplier_invoices")
    op.drop_table("supplier_invoices")
    op.drop_index(
        "ix_purchase_order_lines_supplier_quote_line_id",
        table_name="purchase_order_lines",
    )
    op.drop_index(
        "ix_purchase_order_lines_purchase_order_id",
        table_name="purchase_order_lines",
    )
    op.drop_table("purchase_order_lines")
    op.drop_index("ix_purchase_orders_created_at", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_status", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_customer_offer_id", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_tender_id", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_supplier_id", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_column("supplier_quote_lines", "accepted_at")
    op.drop_column("supplier_quote_lines", "is_accepted")
    op.drop_column("supplier_quotes", "accepted_by")
    op.drop_column("supplier_quotes", "accepted_at")
