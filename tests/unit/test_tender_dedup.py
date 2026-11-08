"""Cross-portal tender fingerprint and ingestion deduplication tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.models.enums import ProcurementProcedure
from src.models.schemas import Tender
from src.repositories.tender_repository import StoredTender, TenderRepositoryError
from src.scrapers.ingestion import PortalIngestionBridge
from src.scrapers.models import DiscoveredTender
from src.services.tender_dedup import (
    normalize_dedup_text,
    tender_dedup_hash,
    tender_fingerprint,
)


def make_discovered(
    *,
    portal: str = "vergabe.bayern",
    portal_code: str = "BY",
    portal_id: str = "2026-001",
    title: str = "Reinigung von Verwaltungsgebäuden",
    client: str = "Stadt München",
    deadline: datetime = datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
) -> DiscoveredTender:
    return DiscoveredTender(
        portal=portal,
        portal_code=portal_code,
        portal_id=portal_id,
        title=title,
        client_name=client,
        detail_url=f"https://{portal}/ausschreibung/{portal_id}",
        submission_deadline=deadline,
        cpv_codes=("33760000-5",),
        procedure=ProcurementProcedure.OPEN_PROCEDURE,
    )


class FakeRepository:
    """Small in-memory repository implementing both unique keys."""

    def __init__(self) -> None:
        self.by_number: dict[str, StoredTender] = {}
        self.by_hash: dict[str, StoredTender] = {}
        self.save_calls = 0
        self.fail_next_save = False

    async def get_by_number(self, tender_number: str) -> StoredTender | None:
        return self.by_number.get(tender_number)

    async def get_by_dedup_hash(self, dedup_hash: str) -> StoredTender | None:
        return self.by_hash.get(dedup_hash)

    async def save(self, tender: Tender, **kwargs: Any) -> StoredTender:
        self.save_calls += 1
        if self.fail_next_save:
            self.fail_next_save = False
            raise TenderRepositoryError("duplicate fingerprint race")
        stored = StoredTender(tender=tender)
        self.by_number[tender.tender_number] = stored
        self.by_hash[tender_fingerprint(tender)] = stored
        return stored


def test_dedup_normalization_ignores_case_punctuation_and_umlauts() -> None:
    assert normalize_dedup_text("  Nitril-Handschuhe  ") == "nitril handschuhe"
    first = tender_dedup_hash(
        "Reinigung & Hygiene",
        "Stadt München",
        datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
    )
    second = tender_dedup_hash(
        "REINIGUNG UND HYGIENE",
        "STADT MUENCHEN",
        datetime(2026, 3, 31, 18, 0, tzinfo=UTC),
    )
    assert first == second


def test_dedup_hash_includes_deadline_and_is_not_a_title_hash() -> None:
    base = tender_dedup_hash(
        "Reinigung",
        "Stadt München",
        datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
    )
    assert base != tender_dedup_hash(
        "Reinigung",
        "Stadt München",
        datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    )
    assert base != tender_dedup_hash(
        "Reinigung",
        "Stadt Nürnberg",
        datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
    )


async def test_cross_portal_duplicate_is_skipped_before_insert() -> None:
    repository = FakeRepository()
    bridge = PortalIngestionBridge(repository)  # type: ignore[arg-type]

    first = await bridge.ingest(make_discovered())
    syndicated = await bridge.ingest(
        make_discovered(
            portal="oeffentlichevergabe.de",
            portal_code="OV",
            portal_id="notice-9001",
        )
    )

    assert first.created is True
    assert syndicated.created is False
    assert syndicated.deduplicated is True
    assert syndicated.duplicate_of == first.tender_id
    assert syndicated.duplicate_reason == "cross_portal_fingerprint"
    assert syndicated.tender_number == first.tender_number
    assert repository.save_calls == 1


async def test_tender_number_takes_precedence_over_fingerprint() -> None:
    repository = FakeRepository()
    bridge = PortalIngestionBridge(repository)  # type: ignore[arg-type]

    await bridge.ingest(make_discovered())
    same_portal = await bridge.ingest(make_discovered())

    assert same_portal.created is False
    assert same_portal.deduplicated is False
    assert same_portal.duplicate_reason == "tender_number"
    assert repository.save_calls == 1


async def test_fingerprint_race_is_resolved_by_recheck() -> None:
    repository = FakeRepository()
    bridge = PortalIngestionBridge(repository)  # type: ignore[arg-type]
    first = await bridge.ingest(make_discovered())

    # Another worker inserted the fingerprint between our pre-check and save.
    repository.fail_next_save = True
    stored = repository.by_number[first.tender_number]
    repository.by_hash[tender_fingerprint(stored.tender)] = stored

    raced = await bridge.ingest(
        make_discovered(
            portal="oeffentlichevergabe.de",
            portal_code="OV",
            portal_id="notice-race",
        )
    )

    assert raced.created is False
    assert raced.deduplicated is True
    assert raced.tender_id == first.tender_id
