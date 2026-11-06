"""Persistence repositories."""

from __future__ import annotations

from src.repositories.catalog_repository import (
    CatalogRepository,
    CatalogSeedItem,
    CatalogSeedResult,
)
from src.repositories.ingestion_repository import (
    IngestionJobView,
    ScraperStateRepository,
    TenderIngestionJobRepository,
)
from src.repositories.invoice_repository import (
    InvoiceNumberExhaustedError,
    InvoiceRepository,
    InvoiceRepositoryConflict,
    InvoiceRepositoryError,
    StoredInvoice,
)
from src.repositories.offer_repository import (
    OfferRepository,
    OfferRepositoryConflict,
    OfferRepositoryError,
    StoredOffer,
    offer_to_orm,
    orm_to_offer,
)
from src.repositories.tender_repository import (
    StoredTender,
    TenderRepository,
    TenderRepositoryError,
    orm_to_tender,
    tender_to_orm,
)
from src.repositories.user_repository import (
    AuthenticationError,
    RefreshTokenRepository,
    RefreshTokenReuseError,
    TokenBlocklistRepository,
    UserRepository,
    UserRepositoryError,
    UserView,
)
from src.repositories.worker_run_repository import (
    WorkerRunRepository,
    WorkerRunRepositoryError,
    WorkerRunView,
)

__all__ = [
    "AuthenticationError",
    "CatalogRepository",
    "CatalogSeedItem",
    "CatalogSeedResult",
    "IngestionJobView",
    "InvoiceNumberExhaustedError",
    "InvoiceRepository",
    "InvoiceRepositoryConflict",
    "InvoiceRepositoryError",
    "OfferRepository",
    "OfferRepositoryConflict",
    "OfferRepositoryError",
    "RefreshTokenRepository",
    "RefreshTokenReuseError",
    "ScraperStateRepository",
    "StoredInvoice",
    "StoredOffer",
    "StoredTender",
    "TenderIngestionJobRepository",
    "TenderRepository",
    "TenderRepositoryError",
    "TokenBlocklistRepository",
    "UserRepository",
    "UserRepositoryError",
    "UserView",
    "WorkerRunRepository",
    "WorkerRunRepositoryError",
    "WorkerRunView",
    "offer_to_orm",
    "orm_to_offer",
    "orm_to_tender",
    "tender_to_orm",
]
