"""initial schema: tenders, supplier quotes, customer offers, pgvector, audit log

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-01-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from src.models.enums import (
    AuditEventType,
    Currency,
    CustomerOfferStatus,
    GloveSize,
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    SupplierQuoteStatus,
    TenderSourceFormat,
    TenderStatus,
    UnitOfMeasure,
)

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(enum_cls: type[Any], name: str, length: int = 32) -> sa.Enum:
    """VARCHAR-backed enum, mirroring ``src.models.orm._enum``."""

    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=False,
        values_callable=lambda cls: [member.value for member in cls],
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # Suppliers
    # ------------------------------------------------------------------
    op.create_table(
        "suppliers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("erpnext_supplier_id", sa.String(length=140), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        sa.UniqueConstraint("name", name="uq_suppliers_name"),
        sa.UniqueConstraint("erpnext_supplier_id", name="uq_suppliers_erpnext_supplier_id"),
    )

    # ------------------------------------------------------------------
    # Tenders
    # ------------------------------------------------------------------
    op.create_table(
        "tenders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tender_number", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("client_name", sa.String(length=256), nullable=False),
        sa.Column("client_contact_email", sa.String(length=320), nullable=True),
        sa.Column("procedure", _enum(ProcurementProcedure, "procurement_procedure"), nullable=False),
        sa.Column("source_format", _enum(TenderSourceFormat, "tender_source_format"), nullable=False),
        sa.Column("source_reference", sa.String(length=512), nullable=True),
        sa.Column(
            "cpv_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("submission_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "currency",
            _enum(Currency, "currency", length=3),
            server_default=sa.text("'EUR'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(TenderStatus, "tender_status"),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column("vat_rate", sa.Numeric(precision=6, scale=4), server_default=sa.text("0.1900"), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenders"),
        sa.UniqueConstraint("tender_number", name="uq_tenders_tender_number"),
        sa.CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="vat_rate_range"),
    )
    op.create_index("ix_tenders_submission_deadline", "tenders", ["submission_deadline"])
    op.create_index("ix_tenders_status", "tenders", ["status"])

    # ------------------------------------------------------------------
    # Tender items
    # ------------------------------------------------------------------
    op.create_table(
        "tender_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_number", sa.String(length=64), nullable=False),
        sa.Column("lot", sa.String(length=64), nullable=True),
        sa.Column("short_text", sa.String(length=512), nullable=False),
        sa.Column("long_text", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit", _enum(UnitOfMeasure, "unit_of_measure"), nullable=False),
        sa.Column(
            "glove_size",
            _enum(GloveSize, "glove_size"),
            server_default=sa.text("'NOT_APPLICABLE'"),
            nullable=False,
        ),
        sa.Column("material", _enum(MaterialType, "material_type"), nullable=True),
        sa.Column("color", _enum(StandardizedColor, "standardized_color"), nullable=True),
        sa.Column("thickness_um", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("dimensions", sa.String(length=128), nullable=True),
        sa.Column("pack_size", sa.Integer(), nullable=True),
        sa.Column("packs_per_carton", sa.Integer(), nullable=True),
        sa.Column("packaging_description", sa.String(length=256), nullable=True),
        sa.Column("delivery_location", sa.String(length=256), nullable=True),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("cpv_code", sa.String(length=16), nullable=True),
        sa.Column("is_alternative_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.String(length=256), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tender_items"),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tenders.id"], name="fk_tender_items_tender_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tender_id", "lot", "position_number", name="uq_tender_lot_position"),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint(
            "thickness_um IS NULL OR (thickness_um > 0 AND thickness_um <= 2000)",
            name="thickness_range",
        ),
        sa.CheckConstraint("pack_size IS NULL OR pack_size > 0", name="pack_size_positive"),
    )
    op.create_index("ix_tender_items_tender_id", "tender_items", ["tender_id"])
    op.create_index("ix_tender_items_position", "tender_items", ["tender_id", "position_number"])

    # ------------------------------------------------------------------
    # Supplier quotes
    # ------------------------------------------------------------------
    op.create_table(
        "supplier_quotes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_name", sa.String(length=256), nullable=False),
        sa.Column("supplier_contact_email", sa.String(length=320), nullable=True),
        sa.Column("quote_reference", sa.String(length=128), nullable=False),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            _enum(SupplierQuoteStatus, "supplier_quote_status"),
            server_default=sa.text("'RECEIVED'"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            _enum(Currency, "quote_currency", length=3),
            server_default=sa.text("'EUR'"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("payment_terms", sa.String(length=256), nullable=True),
        sa.Column("incoterms", sa.String(length=32), nullable=True),
        sa.Column("delivery_days", sa.Integer(), nullable=True),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("source_document", sa.String(length=512), nullable=True),
        sa.Column(
            "raw_extraction",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_quotes"),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"], name="fk_supplier_quotes_supplier_id", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tenders.id"], name="fk_supplier_quotes_tender_id", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("supplier_id", "quote_reference", name="uq_supplier_quote_reference"),
        sa.CheckConstraint("total_net IS NULL OR total_net >= 0", name="total_net_non_negative"),
    )
    op.create_index("ix_supplier_quotes_supplier_id", "supplier_quotes", ["supplier_id"])
    op.create_index("ix_supplier_quotes_tender_id", "supplier_quotes", ["tender_id"])
    op.create_index("ix_supplier_quotes_valid_until", "supplier_quotes", ["valid_until"])

    # ------------------------------------------------------------------
    # Supplier quote lines & tiers
    # ------------------------------------------------------------------
    op.create_table(
        "supplier_quote_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("supplier_quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("tender_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("material", _enum(MaterialType, "line_material_type"), nullable=True),
        sa.Column("color", _enum(StandardizedColor, "line_standardized_color"), nullable=True),
        sa.Column(
            "glove_size",
            _enum(GloveSize, "line_glove_size"),
            server_default=sa.text("'NOT_APPLICABLE'"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit", _enum(UnitOfMeasure, "line_unit_of_measure"), nullable=False),
        sa.Column("unit_price_net", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("moq", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("pack_size", sa.Integer(), nullable=True),
        sa.Column("gtin", sa.String(length=14), nullable=True),
        sa.Column("delivery_days", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_quote_lines"),
        sa.ForeignKeyConstraint(
            ["supplier_quote_id"],
            ["supplier_quotes.id"],
            name="fk_supplier_quote_lines_supplier_quote_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tender_item_id"],
            ["tender_items.id"],
            name="fk_supplier_quote_lines_tender_item_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("supplier_quote_id", "line_number", name="uq_supplier_quote_line_number"),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint("moq IS NULL OR moq > 0", name="moq_positive"),
        sa.CheckConstraint("unit_price_net >= 0", name="unit_price_non_negative"),
        sa.CheckConstraint(
            "gtin IS NULL OR gtin ~ '^(\\d{8}|\\d{12,14})$'",
            name="gtin_format",
        ),
    )
    op.create_index("ix_supplier_quote_lines_supplier_quote_id", "supplier_quote_lines", ["supplier_quote_id"])
    op.create_index("ix_supplier_quote_lines_tender_item_id", "supplier_quote_lines", ["tender_item_id"])

    op.create_table(
        "supplier_quote_tiers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("supplier_quote_line_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("min_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit_price_net", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_quote_tiers"),
        sa.ForeignKeyConstraint(
            ["supplier_quote_line_id"],
            ["supplier_quote_lines.id"],
            name="fk_supplier_quote_tiers_supplier_quote_line_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("supplier_quote_line_id", "min_quantity", name="uq_supplier_quote_tier_quantity"),
        sa.CheckConstraint("min_quantity > 0", name="min_quantity_positive"),
        sa.CheckConstraint("unit_price_net >= 0", name="tier_price_non_negative"),
    )
    op.create_index(
        "ix_supplier_quote_tiers_supplier_quote_line_id",
        "supplier_quote_tiers",
        ["supplier_quote_line_id"],
    )

    # ------------------------------------------------------------------
    # Customer offers
    # ------------------------------------------------------------------
    op.create_table(
        "customer_offers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("offer_number", sa.String(length=128), nullable=False),
        sa.Column("customer_name", sa.String(length=256), nullable=False),
        sa.Column("customer_contact_email", sa.String(length=320), nullable=True),
        sa.Column("customer_reference", sa.String(length=128), nullable=True),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            _enum(CustomerOfferStatus, "customer_offer_status"),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            _enum(Currency, "offer_currency", length=3),
            server_default=sa.text("'EUR'"),
            nullable=False,
        ),
        sa.Column("margin_rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("vat_rate", sa.Numeric(precision=6, scale=4), server_default=sa.text("0.1900"), nullable=False),
        sa.Column("total_vat", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("total_gross", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("payment_terms", sa.String(length=256), nullable=True),
        sa.Column("delivery_terms", sa.String(length=256), nullable=True),
        sa.Column("ed25519_approval_signature", sa.String(length=128), nullable=True),
        sa.Column("approval_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("approved_by", sa.String(length=256), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erpnext_quotation_name", sa.String(length=140), nullable=True),
        sa.Column("gaeb_export_path", sa.String(length=512), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_customer_offers"),
        sa.ForeignKeyConstraint(
            ["tender_id"], ["tenders.id"], name="fk_customer_offers_tender_id", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("offer_number", name="uq_customer_offers_offer_number"),
        sa.UniqueConstraint("erpnext_quotation_name", name="uq_customer_offers_erpnext_quotation_name"),
        sa.CheckConstraint("margin_rate >= 0 AND margin_rate <= 5", name="margin_rate_range"),
        sa.CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="vat_rate_range"),
        sa.CheckConstraint(
            "total_net >= 0 AND total_vat >= 0 AND total_gross >= 0",
            name="totals_non_negative",
        ),
        sa.CheckConstraint("total_gross = total_net + total_vat", name="totals_consistent"),
        sa.CheckConstraint(
            "ed25519_approval_signature IS NULL OR ed25519_approval_signature ~ '^[0-9a-f]{128}$'",
            name="approval_signature_format",
        ),
        sa.CheckConstraint(
            "approval_payload_hash IS NULL OR approval_payload_hash ~ '^[0-9a-f]{64}$'",
            name="approval_hash_format",
        ),
        sa.CheckConstraint(
            """
            status NOT IN ('APPROVED', 'SENT', 'ACCEPTED')
            OR (
                ed25519_approval_signature IS NOT NULL
                AND approval_payload_hash IS NOT NULL
                AND approved_by IS NOT NULL
                AND approved_at IS NOT NULL
            )
            """,
            name="approval_required",
        ),
        sa.CheckConstraint(
            """
            (erpnext_quotation_name IS NULL AND gaeb_export_path IS NULL)
            OR (
                ed25519_approval_signature IS NOT NULL
                AND approval_payload_hash IS NOT NULL
                AND approved_by IS NOT NULL
                AND approved_at IS NOT NULL
            )
            """,
            name="approval_required_for_export",
        ),
    )
    op.create_index("ix_customer_offers_tender_id", "customer_offers", ["tender_id"])
    op.create_index("ix_customer_offers_status", "customer_offers", ["status"])
    op.create_index("ix_customer_offers_customer", "customer_offers", ["customer_name"])

    op.create_table(
        "customer_offer_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_number", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("tender_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_quote_line_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit", _enum(UnitOfMeasure, "offer_line_unit_of_measure"), nullable=False),
        sa.Column("cost_unit_price_net", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("margin_rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("unit_price_net", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_customer_offer_lines"),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["customer_offers.id"],
            name="fk_customer_offer_lines_offer_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tender_item_id"],
            ["tender_items.id"],
            name="fk_customer_offer_lines_tender_item_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_quote_line_id"],
            ["supplier_quote_lines.id"],
            name="fk_customer_offer_lines_supplier_quote_line_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("offer_id", "position_number", name="uq_offer_position"),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint(
            "margin_rate >= 0 AND margin_rate <= 5", name="margin_rate_range"
        ),
        sa.CheckConstraint(
            "cost_unit_price_net >= 0 AND unit_price_net >= 0 AND total_net >= 0",
            name="prices_non_negative",
        ),
    )
    op.create_index("ix_customer_offer_lines_offer_id", "customer_offer_lines", ["offer_id"])

    # ------------------------------------------------------------------
    # Product catalogue with pgvector embedding
    # ------------------------------------------------------------------
    op.create_table(
        "catalog_articles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("article_number", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("material", _enum(MaterialType, "catalog_material_type"), nullable=True),
        sa.Column("color", _enum(StandardizedColor, "catalog_standardized_color"), nullable=True),
        sa.Column(
            "glove_size",
            _enum(GloveSize, "catalog_glove_size"),
            server_default=sa.text("'NOT_APPLICABLE'"),
            nullable=False,
        ),
        sa.Column("unit", _enum(UnitOfMeasure, "catalog_unit_of_measure"), nullable=True),
        sa.Column("unit_price_net", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("pack_size", sa.Integer(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_articles"),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name="fk_catalog_articles_supplier_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("supplier_id", "article_number", name="uq_catalog_supplier_article"),
        sa.CheckConstraint(
            "unit_price_net IS NULL OR unit_price_net >= 0",
            name="unit_price_non_negative",
        ),
    )
    op.create_index("ix_catalog_articles_supplier_id", "catalog_articles", ["supplier_id"])
    op.create_index(
        "ix_catalog_articles_embedding_hnsw",
        "catalog_articles",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # ------------------------------------------------------------------
    # Append-only, hash-chained audit log
    # ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("event_type", _enum(AuditEventType, "audit_event_type"), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.UniqueConstraint("entry_hash", name="uq_audit_logs_entry_hash"),
        sa.CheckConstraint("entry_hash ~ '^[0-9a-f]{64}$'", name="entry_hash_format"),
        sa.CheckConstraint(
            "prev_hash IS NULL OR prev_hash ~ '^[0-9a-f]{64}$'",
            name="prev_hash_format",
        ),
        sa.CheckConstraint(
            "signature IS NULL OR signature ~ '^[0-9a-f]{128}$'",
            name="signature_format",
        ),
    )
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])

    # Audit rows are immutable: block UPDATE and DELETE at the database level.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION yasmina_reject_audit_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION yasmina_reject_audit_log_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS yasmina_reject_audit_log_mutation()")

    op.drop_table("audit_logs")
    op.drop_table("catalog_articles")
    op.drop_table("customer_offer_lines")
    op.drop_table("customer_offers")
    op.drop_table("supplier_quote_tiers")
    op.drop_table("supplier_quote_lines")
    op.drop_table("supplier_quotes")
    op.drop_table("tender_items")
    op.drop_table("tenders")
    op.drop_table("suppliers")
