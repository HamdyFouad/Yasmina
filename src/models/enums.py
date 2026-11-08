"""Canonical domain enumerations for Project Yasmina.

All enums subclass :class:`enum.StrEnum`, so members are plain ``str`` values at
runtime (JSON/DB friendly) while Pydantic v2 ``strict=True`` models only accept
actual enum members — never the raw German text extracted by parsers or LLMs.
Raw text must be mapped here via :mod:`src.parsers.normalizers`.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AuditEventType",
    "Currency",
    "CustomerOfferStatus",
    "GloveSize",
    "InvoiceStatus",
    "MaterialType",
    "ProcurementProcedure",
    "Role",
    "StandardizedColor",
    "SupplierQuoteStatus",
    "TenderSourceFormat",
    "TenderStatus",
    "UnitOfMeasure",
]


class GloveSize(StrEnum):
    """EN 420 / EN 388 protective-glove sizes.

    The numeric EN 420 scale (5 … 11) is mapped onto these buckets by
    :func:`src.parsers.normalizers.normalize_glove_size`.
    """

    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"
    XXL = "XXL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Role(StrEnum):
    """API authorization roles."""

    ADMIN = "admin"
    SALES = "sales"
    APPROVER = "approver"
    VIEWER = "viewer"


class StandardizedColor(StrEnum):
    """Normalized colour vocabulary for gloves, bags and paper products."""

    BLUE = "BLUE"
    BLACK = "BLACK"
    YELLOW = "YELLOW"
    RED = "RED"
    GREEN = "GREEN"
    GREY = "GREY"
    BROWN = "BROWN"
    ORANGE = "ORANGE"
    PINK = "PINK"
    PURPLE = "PURPLE"
    WHITE = "WHITE"
    TRANSPARENT = "TRANSPARENT"
    CLEAR = "CLEAR"
    NATURAL = "NATURAL"
    PRINTED_MULTICOLOR = "PRINTED_MULTICOLOR"
    UNKNOWN = "UNKNOWN"


class MaterialType(StrEnum):
    """Normalized material vocabulary for gloves, bags and paper towels."""

    NITRILE = "NITRILE"
    LATEX = "LATEX"
    VINYL = "VINYL"
    NEOPRENE = "NEOPRENE"
    BUTYL = "BUTYL"
    PVC = "PVC"
    POLYETHYLENE = "POLYETHYLENE"
    POLYPROPYLENE = "POLYPROPYLENE"
    PAPER = "PAPER"
    RECYCLED_PAPER = "RECYCLED_PAPER"
    COTTON = "COTTON"
    MICROFIBER = "MICROFIBER"
    MIXED = "MIXED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class UnitOfMeasure(StrEnum):
    """Normalized packaging / measurement units used by the pricing engine."""

    STUECK = "STUECK"
    PACK = "PACK"
    KARTON = "KARTON"
    PALETTE = "PALETTE"
    ROLLE = "ROLLE"
    KILOGRAMM = "KILOGRAMM"
    LITER = "LITER"
    METER = "METER"
    QUADRATMETER = "QUADRATMETER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Currency(StrEnum):
    """ISO-4217 currencies supported by the trading engine."""

    EUR = "EUR"
    USD = "USD"
    CHF = "CHF"


class TenderSourceFormat(StrEnum):
    """Origin format of an ingested tender."""

    GAEB_X83 = "GAEB_X83"
    GAEB_D83 = "GAEB_D83"
    GAEB_X84 = "GAEB_X84"
    PDF_DOCLING = "PDF_DOCLING"
    PORTAL = "PORTAL"
    API = "API"
    MANUAL = "MANUAL"


class TenderStatus(StrEnum):
    """Lifecycle of a tender (Ausschreibung)."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"
    IN_EVALUATION = "IN_EVALUATION"
    AWARDED = "AWARDED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class ProcurementProcedure(StrEnum):
    """German procurement procedure types (Vergabeart)."""

    OPEN_PROCEDURE = "OPEN_PROCEDURE"
    RESTRICTED_PROCEDURE = "RESTRICTED_PROCEDURE"
    NEGOTIATED_PROCEDURE = "NEGOTIATED_PROCEDURE"
    COMPETITIVE_DIALOGUE = "COMPETITIVE_DIALOGUE"
    DIRECT_AWARD = "DIRECT_AWARD"


class SupplierQuoteStatus(StrEnum):
    """Lifecycle of an inbound supplier quotation."""

    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class CustomerOfferStatus(StrEnum):
    """Lifecycle of an outbound customer offer (Angebot)."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"


class AuditEventType(StrEnum):
    """Event vocabulary for the append-only, hash-chained audit log."""

    TENDER_INGESTED = "TENDER_INGESTED"
    TENDER_ITEM_NORMALIZED = "TENDER_ITEM_NORMALIZED"
    SUPPLIER_QUOTE_INGESTED = "SUPPLIER_QUOTE_INGESTED"
    OFFER_PRICED = "OFFER_PRICED"
    OFFER_APPROVED = "OFFER_APPROVED"
    OFFER_REJECTED = "OFFER_REJECTED"
    INVOICE_ISSUED = "INVOICE_ISSUED"
    INVOICE_PAYMENT_RECORDED = "INVOICE_PAYMENT_RECORDED"
    INVOICE_CREDITED = "INVOICE_CREDITED"
    ERP_QUOTATION_CREATED = "ERP_QUOTATION_CREATED"
    RFQ_DISPATCHED = "RFQ_DISPATCHED"
    GAEB_EXPORTED = "GAEB_EXPORTED"
    CLAMAV_SCAN_CLEAN = "CLAMAV_SCAN_CLEAN"
    CLAMAV_SCAN_REJECTED = "CLAMAV_SCAN_REJECTED"
    SYSTEM_SECURITY_ALERT = "SYSTEM_SECURITY_ALERT"
    USER_LOGIN_SUCCEEDED = "USER_LOGIN_SUCCEEDED"
    USER_LOGIN_FAILED = "USER_LOGIN_FAILED"
    USER_LOGOUT = "USER_LOGOUT"
    USER_LOGGED_OUT_ALL_DEVICES = "USER_LOGGED_OUT_ALL_DEVICES"
    USER_CREATED = "USER_CREATED"
    USER_PASSWORD_CHANGED = "USER_PASSWORD_CHANGED"
    USER_PASSWORD_RESET_REQUESTED = "USER_PASSWORD_RESET_REQUESTED"
    USER_PASSWORD_RESET_COMPLETED = "USER_PASSWORD_RESET_COMPLETED"
    USER_MFA_ENABLED = "USER_MFA_ENABLED"
    USER_MFA_DISABLED = "USER_MFA_DISABLED"
    SYSTEM_TOKEN_CLEANUP = "SYSTEM_TOKEN_CLEANUP"
    SYSTEM_BACKUP_COMPLETED = "SYSTEM_BACKUP_COMPLETED"
    SYSTEM_CATALOG_REINDEXED = "SYSTEM_CATALOG_REINDEXED"
    SYSTEM_MIGRATION = "SYSTEM_MIGRATION"


class InvoiceStatus(StrEnum):
    """Payment and credit lifecycle of an issued invoice."""

    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    PARTIALLY_CREDITED = "PARTIALLY_CREDITED"
    CREDITED = "CREDITED"
    CANCELLED = "CANCELLED"
