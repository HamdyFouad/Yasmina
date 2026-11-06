"""SQLAlchemy 2.0 ORM models for Project Yasmina (PostgreSQL + pgvector).

The mapping is deliberately explicit: no implicit naming, no implicit enum
storage. Every enum is persisted as ``VARCHAR`` plus a CHECK constraint
(``native_enum=False``), which keeps migrations cheap and reversible.

Audit log
---------
``audit_logs`` is append-only and hash-chained:

``entry_hash = sha256(canonical_json({...}))``

where the hashed payload contains an ISO-8601 UTC ``occurred_at``,
``event_type``, ``actor``, ``entity_type``, ``entity_id``, ``payload`` and
``prev_hash``. The database-generated ``id`` is deliberately excluded so the
hash can be computed before insert. Canonical JSON means UTF-8, sorted keys,
``separators=(",", ":")`` and ``ensure_ascii=False``. The database enforces the
chain shape (64-char lowercase hex hashes, unique ``entry_hash``) and blocks
UPDATE/DELETE with a trigger created by the initial migration.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.models.enums import (
    AuditEventType,
    Currency,
    CustomerOfferStatus,
    GloveSize,
    InvoiceStatus,
    MaterialType,
    ProcurementProcedure,
    StandardizedColor,
    SupplierQuoteStatus,
    TenderSourceFormat,
    TenderStatus,
    UnitOfMeasure,
)

__all__ = [
    "AuditLog",
    "Base",
    "CatalogArticle",
    "CreditNote",
    "CreditNoteNumberSequence",
    "CustomerOffer",
    "CustomerOfferLine",
    "IntegrationIdempotency",
    "Invoice",
    "InvoiceEvent",
    "InvoiceLine",
    "InvoiceNumberSequence",
    "InvoiceVatSplit",
    "PasswordResetToken",
    "RefreshToken",
    "Supplier",
    "SupplierQuote",
    "SupplierQuoteLine",
    "SupplierQuoteTier",
    "Tender",
    "TenderIngestionJob",
    "TenderItem",
    "ScraperState",
    "TokenBlocklist",
    "User",
    "WorkerRun",
    "WorkflowCheckpoint",
    "WorkflowCheckpointBlob",
    "WorkflowCheckpointWrite",
]

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with the naming convention Alembic relies on."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enum(enum_cls: type[Any], name: str, length: int = 32) -> SAEnum:
    """Portable enum column: VARCHAR storing enum values (not member names).

    Enum membership is enforced by the strict Pydantic schemas and the
    normalizer layer; the database stores the canonical string value.
    """

    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# Suppliers & tenders
# ---------------------------------------------------------------------------


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    erpnext_supplier_id: Mapped[str | None] = mapped_column(String(140), nullable=True, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    quotes: Mapped[list[SupplierQuote]] = relationship(back_populates="supplier", lazy="selectin")
    catalog_articles: Mapped[list[CatalogArticle]] = relationship(back_populates="supplier", lazy="selectin")


class Tender(Base, TimestampMixin):
    __tablename__ = "tenders"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    tender_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    client_name: Mapped[str] = mapped_column(String(256), nullable=False)
    client_contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    procedure: Mapped[ProcurementProcedure] = mapped_column(
        _enum(ProcurementProcedure, "procurement_procedure"), nullable=False
    )
    source_format: Mapped[TenderSourceFormat] = mapped_column(
        _enum(TenderSourceFormat, "tender_source_format"), nullable=False
    )
    source_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cpv_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    submission_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    currency: Mapped[Currency] = mapped_column(
        _enum(Currency, "currency", length=3),
        nullable=False,
        default=Currency.EUR,
        server_default=text("'EUR'"),
    )
    status: Mapped[TenderStatus] = mapped_column(
        _enum(TenderStatus, "tender_status"),
        nullable=False,
        default=TenderStatus.DRAFT,
        server_default=text("'DRAFT'"),
    )
    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.1900"), server_default=text("0.1900")
    )

    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    items: Mapped[list[TenderItem]] = relationship(
        back_populates="tender",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TenderItem.position_number",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="vat_rate_range"),
        Index("ix_tenders_submission_deadline", "submission_deadline"),
        Index("ix_tenders_status", "status"),
        Index("ix_tenders_owner", "owner"),
    )


class TenderItem(Base, TimestampMixin):
    __tablename__ = "tender_items"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    tender_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )

    position_number: Mapped[str] = mapped_column(String(64), nullable=False)
    lot: Mapped[str | None] = mapped_column(String(64), nullable=True)

    short_text: Mapped[str] = mapped_column(String(512), nullable=False)
    long_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[UnitOfMeasure] = mapped_column(_enum(UnitOfMeasure, "unit_of_measure"), nullable=False)

    glove_size: Mapped[GloveSize] = mapped_column(
        _enum(GloveSize, "glove_size"),
        nullable=False,
        default=GloveSize.NOT_APPLICABLE,
        server_default=text("'NOT_APPLICABLE'"),
    )
    material: Mapped[MaterialType | None] = mapped_column(_enum(MaterialType, "material_type"), nullable=True)
    color: Mapped[StandardizedColor | None] = mapped_column(
        _enum(StandardizedColor, "standardized_color"), nullable=True
    )

    thickness_um: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    dimensions: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pack_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packs_per_carton: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packaging_description: Mapped[str | None] = mapped_column(String(256), nullable=True)

    delivery_location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    cpv_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_alternative_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    tender: Mapped[Tender] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("tender_id", "lot", "position_number", name="uq_tender_lot_position"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "thickness_um IS NULL OR (thickness_um > 0 AND thickness_um <= 2000)", name="thickness_range"
        ),
        CheckConstraint("pack_size IS NULL OR pack_size > 0", name="pack_size_positive"),
        Index("ix_tender_items_position", "tender_id", "position_number"),
    )


# ---------------------------------------------------------------------------
# Supplier quotations
# ---------------------------------------------------------------------------


class SupplierQuote(Base, TimestampMixin):
    __tablename__ = "supplier_quotes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    supplier_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier_name: Mapped[str] = mapped_column(String(256), nullable=False)
    supplier_contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    quote_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    tender_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[SupplierQuoteStatus] = mapped_column(
        _enum(SupplierQuoteStatus, "supplier_quote_status"),
        nullable=False,
        default=SupplierQuoteStatus.RECEIVED,
        server_default=text("'RECEIVED'"),
    )
    currency: Mapped[Currency] = mapped_column(
        _enum(Currency, "quote_currency", length=3),
        nullable=False,
        default=Currency.EUR,
        server_default=text("'EUR'"),
    )

    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    payment_terms: Mapped[str | None] = mapped_column(String(256), nullable=True)
    incoterms: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    total_net: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    source_document: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_extraction: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    supplier: Mapped[Supplier | None] = relationship(back_populates="quotes")
    lines: Mapped[list[SupplierQuoteLine]] = relationship(
        back_populates="quote",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SupplierQuoteLine.line_number",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("supplier_id", "quote_reference", name="uq_supplier_quote_reference"),
        CheckConstraint("total_net IS NULL OR total_net >= 0", name="total_net_non_negative"),
    )


class SupplierQuoteLine(Base, TimestampMixin):
    __tablename__ = "supplier_quote_lines"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    supplier_quote_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("supplier_quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    description: Mapped[str] = mapped_column(String(512), nullable=False)
    tender_item_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    material: Mapped[MaterialType | None] = mapped_column(
        _enum(MaterialType, "line_material_type"), nullable=True
    )
    color: Mapped[StandardizedColor | None] = mapped_column(
        _enum(StandardizedColor, "line_standardized_color"), nullable=True
    )
    glove_size: Mapped[GloveSize] = mapped_column(
        _enum(GloveSize, "line_glove_size"),
        nullable=False,
        default=GloveSize.NOT_APPLICABLE,
        server_default=text("'NOT_APPLICABLE'"),
    )

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[UnitOfMeasure] = mapped_column(_enum(UnitOfMeasure, "line_unit_of_measure"), nullable=False)
    unit_price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    moq: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    pack_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(14), nullable=True)
    delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    quote: Mapped[SupplierQuote] = relationship(back_populates="lines")
    tiers: Mapped[list[SupplierQuoteTier]] = relationship(
        back_populates="line",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SupplierQuoteTier.min_quantity",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("supplier_quote_id", "line_number", name="uq_supplier_quote_line_number"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("moq IS NULL OR moq > 0", name="moq_positive"),
        CheckConstraint("unit_price_net >= 0", name="unit_price_non_negative"),
        CheckConstraint("gtin IS NULL OR gtin ~ '^(\\d{8}|\\d{12,14})$'", name="gtin_format"),
    )


class SupplierQuoteTier(Base):
    __tablename__ = "supplier_quote_tiers"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    supplier_quote_line_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("supplier_quote_lines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    min_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    line: Mapped[SupplierQuoteLine] = relationship(back_populates="tiers")

    __table_args__ = (
        UniqueConstraint("supplier_quote_line_id", "min_quantity", name="uq_supplier_quote_tier_quantity"),
        CheckConstraint("min_quantity > 0", name="min_quantity_positive"),
        CheckConstraint("unit_price_net >= 0", name="tier_price_non_negative"),
    )


# ---------------------------------------------------------------------------
# Customer offers
# ---------------------------------------------------------------------------


class CustomerOffer(Base, TimestampMixin):
    __tablename__ = "customer_offers"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    offer_number: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    customer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    customer_contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    customer_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tender_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenders.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[CustomerOfferStatus] = mapped_column(
        _enum(CustomerOfferStatus, "customer_offer_status"),
        nullable=False,
        default=CustomerOfferStatus.DRAFT,
        server_default=text("'DRAFT'"),
    )
    currency: Mapped[Currency] = mapped_column(
        _enum(Currency, "offer_currency", length=3),
        nullable=False,
        default=Currency.EUR,
        server_default=text("'EUR'"),
    )

    margin_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    total_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.1900"), server_default=text("0.1900")
    )
    total_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    payment_terms: Mapped[str | None] = mapped_column(String(256), nullable=True)
    delivery_terms: Mapped[str | None] = mapped_column(String(256), nullable=True)

    ed25519_approval_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approval_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    erpnext_quotation_name: Mapped[str | None] = mapped_column(String(140), nullable=True, unique=True)
    invoice_artifacts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    gaeb_export_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[CustomerOfferLine]] = relationship(
        back_populates="offer",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CustomerOfferLine.position_number",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("margin_rate >= 0 AND margin_rate <= 5", name="margin_rate_range"),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="vat_rate_range"),
        CheckConstraint("total_net >= 0 AND total_vat >= 0 AND total_gross >= 0", name="totals_non_negative"),
        CheckConstraint("total_gross = total_net + total_vat", name="totals_consistent"),
        CheckConstraint(
            "ed25519_approval_signature IS NULL OR ed25519_approval_signature ~ '^[0-9a-f]{128}$'",
            name="approval_signature_format",
        ),
        CheckConstraint(
            "approval_payload_hash IS NULL OR approval_payload_hash ~ '^[0-9a-f]{64}$'",
            name="approval_hash_format",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        Index("ix_customer_offers_status", "status"),
        Index("ix_customer_offers_customer", "customer_name"),
        Index("ix_customer_offers_owner", "owner"),
    )


class CustomerOfferLine(Base, TimestampMixin):
    __tablename__ = "customer_offer_lines"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    offer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customer_offers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    position_number: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)

    tender_item_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_items.id", ondelete="SET NULL"), nullable=True
    )
    supplier_quote_line_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("supplier_quote_lines.id", ondelete="SET NULL"), nullable=True
    )

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[UnitOfMeasure] = mapped_column(
        _enum(UnitOfMeasure, "offer_line_unit_of_measure"), nullable=False
    )

    cost_unit_price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    margin_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    unit_price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    total_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.1900"), server_default=text("0.1900")
    )

    offer: Mapped[CustomerOffer] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("offer_id", "position_number", name="uq_offer_position"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("margin_rate >= 0 AND margin_rate <= 5", name="margin_rate_range"),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="vat_rate_range"),
        CheckConstraint(
            "cost_unit_price_net >= 0 AND unit_price_net >= 0 AND total_net >= 0",
            name="prices_non_negative",
        ),
    )


# ---------------------------------------------------------------------------
# Issued invoices, gapless numbering and multi-rate VAT records
# ---------------------------------------------------------------------------


class InvoiceNumberSequence(Base, TimestampMixin):
    """Transactionally incremented yearly invoice counter.

    A PostgreSQL sequence would leave gaps when an invoice transaction rolls
    back, so the counter is a locked row updated in the same transaction as the
    invoice insert.
    """

    __tablename__ = "invoice_number_sequences"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    __table_args__ = (
        CheckConstraint("year >= 2000 AND year <= 2200", name="year_range"),
        CheckConstraint("last_value >= 0 AND last_value <= 9999", name="last_value_range"),
    )


class Invoice(Base, TimestampMixin):
    """Legally relevant issued invoice linked to one approved customer offer."""

    __tablename__ = "invoices"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    offer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customer_offers.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    invoice_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    invoice_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "invoice_status", length=24),
        nullable=False,
        default=InvoiceStatus.ISSUED,
        server_default=text("'ISSUED'"),
    )
    currency: Mapped[Currency] = mapped_column(
        _enum(Currency, "invoice_currency", length=3),
        nullable=False,
        default=Currency.EUR,
        server_default=text("'EUR'"),
    )

    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    seller_name: Mapped[str] = mapped_column(String(256), nullable=False)
    seller_street: Mapped[str] = mapped_column(String(256), nullable=False)
    seller_city: Mapped[str] = mapped_column(String(128), nullable=False)
    seller_postal_code: Mapped[str] = mapped_column(String(32), nullable=False)
    seller_country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="DE")
    seller_vat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seller_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    seller_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seller_contact_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    buyer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    buyer_street: Mapped[str] = mapped_column(String(256), nullable=False)
    buyer_city: Mapped[str] = mapped_column(String(128), nullable=False)
    buyer_postal_code: Mapped[str] = mapped_column(String(32), nullable=False)
    buyer_country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="DE")
    buyer_vat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    buyer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    buyer_reference: Mapped[str | None] = mapped_column(String(46), nullable=True)
    order_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(512), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)

    total_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    credited_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    credit_note_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    vat_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    artifacts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    offer: Mapped[CustomerOffer] = relationship()
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InvoiceLine.line_id",
        lazy="selectin",
    )
    vat_splits: Mapped[list[InvoiceVatSplit]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InvoiceVatSplit.rate",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("invoice_year", "sequence_number", name="uq_invoice_year_sequence"),
        CheckConstraint(
            "invoice_number ~ '^INV-[0-9]{4}-[0-9]{4}$'",
            name="invoice_number_format",
        ),
        CheckConstraint("invoice_year >= 2000 AND invoice_year <= 2200", name="invoice_year_range"),
        CheckConstraint("sequence_number >= 1 AND sequence_number <= 9999", name="sequence_range"),
        CheckConstraint(
            "total_net >= 0 AND total_vat >= 0 AND total_gross >= 0",
            name="totals_non_negative",
        ),
        CheckConstraint("total_gross = total_net + total_vat", name="totals_consistent"),
        CheckConstraint(
            "paid_amount >= 0 AND credited_amount >= 0",
            name="payment_amounts_non_negative",
        ),
        CheckConstraint("paid_amount <= total_gross", name="paid_amount_bounded"),
        CheckConstraint("credited_amount <= total_gross", name="credited_amount_bounded"),
        CheckConstraint(
            "paid_amount + credited_amount <= total_gross",
            name="payment_plus_credit_bounded",
        ),
        CheckConstraint("issue_date <= due_date", name="due_date_after_issue"),
        Index("ix_invoices_status_due", "status", "due_date"),
        Index("ix_invoices_issue_date", "issue_date"),
    )


class InvoiceLine(Base, TimestampMixin):
    __tablename__ = "invoice_lines"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    invoice_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )

    line_id: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[UnitOfMeasure] = mapped_column(
        _enum(UnitOfMeasure, "invoice_line_unit_of_measure"), nullable=False
    )
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("invoice_id", "line_id", name="uq_invoice_line"),
        CheckConstraint("line_id >= 1", name="line_id_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0 AND line_total >= 0", name="prices_non_negative"),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="vat_rate_range"),
    )


class InvoiceVatSplit(Base, TimestampMixin):
    """One persisted VAT bucket (e.g. 19 %, 7 %, 0 %) for an invoice."""

    __tablename__ = "invoice_vat_splits"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    invoice_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="vat_splits")

    __table_args__ = (
        UniqueConstraint("invoice_id", "rate", name="uq_invoice_vat_rate"),
        CheckConstraint("rate >= 0 AND rate <= 1", name="rate_range"),
        CheckConstraint("taxable_amount >= 0 AND tax_amount >= 0", name="amounts_non_negative"),
    )


class CreditNoteNumberSequence(Base, TimestampMixin):
    """Transactionally incremented yearly ``GS-YYYY-XXXX`` counter."""

    __tablename__ = "credit_note_number_sequences"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    __table_args__ = (
        CheckConstraint("year >= 2000 AND year <= 2200", name="year_range"),
        CheckConstraint("last_value >= 0 AND last_value <= 9999", name="last_value_range"),
    )


class CreditNote(Base, TimestampMixin):
    """GoBD-relevant credit note with its own gapless numbering sequence."""

    __tablename__ = "credit_notes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    invoice_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    credit_note_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    credit_note_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="APPLIED", server_default=text("'APPLIED'")
    )
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    total_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    lines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    artifacts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    invoice: Mapped[Invoice] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "credit_note_year",
            "sequence_number",
            name="uq_credit_note_year_sequence",
        ),
        CheckConstraint(
            "credit_note_number ~ '^GS-[0-9]{4}-[0-9]{4}$'",
            name="credit_note_number_format",
        ),
        CheckConstraint(
            "credit_note_year >= 2000 AND credit_note_year <= 2200",
            name="credit_note_year_range",
        ),
        CheckConstraint(
            "sequence_number >= 1 AND sequence_number <= 9999",
            name="sequence_range",
        ),
        CheckConstraint(
            "status IN ('ISSUED', 'APPLIED', 'CANCELLED')",
            name="status_valid",
        ),
        CheckConstraint(
            "total_net >= 0 AND total_vat >= 0 AND total_gross >= 0",
            name="totals_non_negative",
        ),
        CheckConstraint("total_gross = total_net + total_vat", name="totals_consistent"),
        Index("ix_credit_notes_invoice_date", "invoice_id", "issue_date"),
    )


class InvoiceEvent(Base):
    """Append-only, hash-chained invoice lifecycle event (GoBD retention)."""

    __tablename__ = "invoice_events"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    invoice_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    invoice: Mapped[Invoice] = relationship()

    __table_args__ = (
        UniqueConstraint("invoice_id", "sequence", name="uq_invoice_event_sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "event_type IN ('ISSUED', 'PAYMENT_RECORDED', 'CREDIT_ISSUED', 'STATUS_CHANGED', 'CANCELLED')",
            name="event_type_valid",
        ),
        CheckConstraint(
            "prev_hash ~ '^[0-9a-f]{64}$' AND entry_hash ~ '^[0-9a-f]{64}$'",
            name="hash_format",
        ),
        CheckConstraint("amount IS NULL OR amount >= 0", name="amount_non_negative"),
        Index("ix_invoice_events_invoice_sequence", "invoice_id", "sequence"),
    )


# ---------------------------------------------------------------------------
# Identity, refresh sessions and revoked access-token JTIs
# ---------------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    roles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    mfa_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    password_reset_tokens: Mapped[list[PasswordResetToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("failed_login_attempts >= 0", name="failed_attempts_non_negative"),
        CheckConstraint("mfa_last_step IS NULL OR mfa_last_step >= 0", name="mfa_last_step_non_negative"),
        Index("ix_users_active", "is_active"),
    )


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_format"),
        Index("ix_refresh_tokens_expires", "expires_at"),
    )


class TokenBlocklist(Base):
    __tablename__ = "token_blocklist"

    jti: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_token_blocklist_expires", "expires_at"),)


class PasswordResetToken(Base, TimestampMixin):
    """Single-use, hashed, short-lived password reset token."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="password_reset_tokens")

    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="password_reset_token_hash_format"),
        Index("ix_password_reset_tokens_expires", "expires_at"),
    )


# ---------------------------------------------------------------------------
# Product catalogue (pgvector matching)
# ---------------------------------------------------------------------------


class CatalogArticle(Base, TimestampMixin):
    """Supplier article used by the semantic matching service.

    ``embedding`` is a 1024-dim vector (BGE-M3) indexed with HNSW cosine
    distance for approximate nearest-neighbour search.
    """

    __tablename__ = "catalog_articles"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    supplier_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    article_number: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)

    material: Mapped[MaterialType | None] = mapped_column(
        _enum(MaterialType, "catalog_material_type"), nullable=True
    )
    color: Mapped[StandardizedColor | None] = mapped_column(
        _enum(StandardizedColor, "catalog_standardized_color"), nullable=True
    )
    glove_size: Mapped[GloveSize] = mapped_column(
        _enum(GloveSize, "catalog_glove_size"),
        nullable=False,
        default=GloveSize.NOT_APPLICABLE,
        server_default=text("'NOT_APPLICABLE'"),
    )
    unit: Mapped[UnitOfMeasure | None] = mapped_column(
        _enum(UnitOfMeasure, "catalog_unit_of_measure"), nullable=True
    )

    unit_price_net: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pack_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[Any | None] = mapped_column(Vector(1024), nullable=True)

    supplier: Mapped[Supplier | None] = relationship(back_populates="catalog_articles")

    __table_args__ = (
        UniqueConstraint("supplier_id", "article_number", name="uq_catalog_supplier_article"),
        CheckConstraint("unit_price_net IS NULL OR unit_price_net >= 0", name="unit_price_non_negative"),
        Index(
            "ix_catalog_articles_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# ---------------------------------------------------------------------------
# External integration idempotency
# ---------------------------------------------------------------------------


class IntegrationIdempotency(Base, TimestampMixin):
    """Tracks outbound webhook and inbound callback idempotency keys.

    A key is reserved before an integration side effect. ``pending`` means a
    concurrent or crashed attempt owns the key, ``succeeded`` stores the exact
    API response for replay, and ``failed`` may be retried explicitly.
    """

    __tablename__ = "integration_idempotency"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_format"),
        Index("ix_integration_idempotency_status", "status"),
        Index("ix_integration_idempotency_operation", "operation"),
    )


# ---------------------------------------------------------------------------
# Portal ingestion outbox and scraper watermarks
# ---------------------------------------------------------------------------


class TenderIngestionJob(Base, TimestampMixin):
    """Durable outbox row for portal metadata/document processing."""

    __tablename__ = "tender_ingestion_jobs"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    tender_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    portal_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default=text("5"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    raw_object: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'skipped')",
            name="status_valid",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_tender_ingestion_jobs_status_available", "status", "available_at"),
    )


class ScraperState(Base, TimestampMixin):
    """Per-portal watermark used to avoid re-fetching old listing pages."""

    __tablename__ = "scraper_states"

    portal_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkerRun(Base, TimestampMixin):
    """Execution history for scraper and ingestion-outbox worker runs."""

    __tablename__ = "worker_runs"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default=text("'running'")
    )
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    errors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(
            "run_type IN ('scrape', 'ingestion')",
            name="run_type_valid",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("items_processed >= 0", name="items_non_negative"),
        CheckConstraint("errors_count >= 0", name="errors_non_negative"),
        Index("ix_worker_runs_started_at", "started_at"),
        Index("ix_worker_runs_type_status", "run_type", "status"),
    )


# ---------------------------------------------------------------------------
# LangGraph workflow checkpoints
# ---------------------------------------------------------------------------


class WorkflowCheckpoint(Base):
    """Durable LangGraph checkpoint header.

    Channel values are stored separately in :class:`WorkflowCheckpointBlob` so
    LangGraph's serializer can round-trip Pydantic/Decimal/UUID state without
    placing business models directly into JSONB.
    """

    __tablename__ = "workflow_checkpoints"

    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_workflow_checkpoints_thread", "thread_id", "checkpoint_ns"),)


class WorkflowCheckpointBlob(Base):
    """Serialized LangGraph channel value for one checkpoint namespace/version."""

    __tablename__ = "workflow_checkpoint_blobs"

    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True)
    channel: Mapped[str] = mapped_column(String(255), primary_key=True)
    version: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkflowCheckpointWrite(Base):
    """Pending LangGraph task write, replayed after a worker restart."""

    __tablename__ = "workflow_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    task_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Append-only audit log
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Append-only, hash-chained audit entry.

    ``id`` doubles as the chain position. ``prev_hash`` is the ``entry_hash``
    of the previous row (``NULL`` for genesis). Rows are immutable: migrations
    install triggers rejecting UPDATE, DELETE and TRUNCATE.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        _enum(AuditEventType, "audit_event_type"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    signature: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint("entry_hash ~ '^[0-9a-f]{64}$'", name="entry_hash_format"),
        CheckConstraint("prev_hash IS NULL OR prev_hash ~ '^[0-9a-f]{64}$'", name="prev_hash_format"),
        CheckConstraint("signature IS NULL OR signature ~ '^[0-9a-f]{128}$'", name="signature_format"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )
