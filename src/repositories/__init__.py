"""Persistence repositories."""

from __future__ import annotations

from src.repositories.ingestion_repository import (
    IngestionJobView,
    ScraperStateRepository,
    TenderIngestionJobRepository,
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

__all__ = [
    "IngestionJobView",
    "OfferRepository",
    "OfferRepositoryConflict",
    "OfferRepositoryError",
    "ScraperStateRepository",
    "StoredOffer",
    "StoredTender",
    "TenderIngestionJobRepository",
    "TenderRepository",
    "TenderRepositoryError",
    "offer_to_orm",
    "orm_to_offer",
    "orm_to_tender",
    "tender_to_orm",
]
