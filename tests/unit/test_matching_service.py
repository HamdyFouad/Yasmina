"""Hybrid two-stage matching tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.models.schemas import TenderItem
from src.services.matching_service import (
    CatalogCandidate,
    HybridMatchingService,
    MatchingError,
    cosine_similarity,
    lexical_similarity,
)


def make_item(**overrides: object) -> TenderItem:
    data: dict[str, object] = {
        "position_number": "1.1",
        "short_text": "Nitrilhandschuhe blau Größe M",
        "quantity": Decimal("10.0000"),
        "unit": UnitOfMeasure.KARTON,
        "material": MaterialType.NITRILE,
        "color": StandardizedColor.BLUE,
        "glove_size": GloveSize.M,
    }
    data.update(overrides)
    return TenderItem(**data)


def candidate(number: str, **overrides: object) -> CatalogCandidate:
    data: dict[str, object] = {
        "id": number,
        "article_number": number,
        "description": "Nitril Einweghandschuhe blau Größe M",
        "material": MaterialType.NITRILE,
        "color": StandardizedColor.BLUE,
        "glove_size": GloveSize.M,
        "unit": UnitOfMeasure.KARTON,
    }
    data.update(overrides)
    return CatalogCandidate(**data)  # type: ignore[arg-type]


def test_cosine_similarity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    with pytest.raises(MatchingError, match="dimensions"):
        cosine_similarity([1.0], [1.0, 2.0])


def test_lexical_similarity_prefers_shared_terms() -> None:
    close = lexical_similarity(
        "Nitrilhandschuhe blau Größe M",
        "Nitril Einweghandschuhe blau Größe M",
    )
    far = lexical_similarity(
        "Nitrilhandschuhe blau Größe M",
        "Papierhandtücher weiß 2-lagig",
    )

    assert close > far
    assert 0.0 <= far <= close <= 1.0


def test_stage_one_filters_incompatible_attributes() -> None:
    service = HybridMatchingService()
    item = make_item()

    accepted, rejected = service.filter_candidates(
        item,
        [
            candidate("MATCH"),
            candidate("LATEX", material=MaterialType.LATEX),
            candidate("BLACK", color=StandardizedColor.BLACK),
            candidate("SIZE-L", glove_size=GloveSize.L),
            candidate("PACK", unit=UnitOfMeasure.PACK),
            candidate("UNKNOWN-ATTRS", material=None, color=None, glove_size=GloveSize.NOT_APPLICABLE, unit=None),
        ],
    )

    assert {entry.article_number for entry in accepted} == {"MATCH", "UNKNOWN-ATTRS"}
    assert len(rejected) == 4


def test_hybrid_ranking_uses_semantic_similarity() -> None:
    service = HybridMatchingService(min_score=0.1)
    item = make_item()
    semantic_match = candidate(
        "SEMANTIC",
        description="Einweghandschuh Nitril blau mittel",
        embedding=(1.0, 0.0, 0.0),
    )
    lexical_match = candidate(
        "LEXICAL",
        description="Nitrilhandschuhe blau Größe M",
        embedding=(0.0, 1.0, 0.0),
    )

    results = service.rank(
        item,
        [lexical_match, semantic_match],
        query_embedding=(1.0, 0.0, 0.0),
    )

    assert results[0].candidate.article_number == "SEMANTIC"
    assert results[0].semantic_score == pytest.approx(1.0)
    assert results[1].semantic_score == pytest.approx(0.0)
    assert "semantic" in results[0].reasons


def test_lexical_fallback_without_embeddings() -> None:
    service = HybridMatchingService(min_score=0.0)
    item = make_item()
    close = candidate("CLOSE", description="Nitrilhandschuhe blau Größe M")
    far = candidate("FAR", description="Papierhandtücher weiß 2-lagig")

    results = service.rank(item, [far, close])

    assert results[0].candidate.article_number == "CLOSE"
    assert results[0].semantic_score is None
    assert "lexical" in results[0].reasons


def test_limit_and_threshold() -> None:
    service = HybridMatchingService(min_score=0.9)
    item = make_item()
    distant = [
        candidate("A", description="Papierhandtücher weiß 2-lagig"),
        candidate("B", description="Müllbeutel schwarz 120 Liter"),
    ]

    results = service.rank(item, distant, query_embedding=(1.0, 0.0))

    assert results == []
    with pytest.raises(MatchingError, match="limit"):
        service.rank(item, [], limit=0)
