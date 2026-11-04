"""In-process tender store for the API layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from src.models.schemas import Tender

__all__ = ["StoredTender", "TenderStore"]


@dataclass(frozen=True, slots=True)
class StoredTender:
    tender: Tender
    document: Any = None
    source_filename: str | None = None
    source_path: Path | None = None


class TenderStore:
    """Small process-local store; replace with a DB repository for multi-worker use."""

    def __init__(self) -> None:
        self._items: dict[UUID, StoredTender] = {}

    def put(self, stored: StoredTender) -> None:
        self._items[stored.tender.id] = stored

    def get(self, tender_id: UUID) -> StoredTender | None:
        return self._items.get(tender_id)

    def delete(self, tender_id: UUID) -> None:
        self._items.pop(tender_id, None)

    def __len__(self) -> int:
        return len(self._items)
