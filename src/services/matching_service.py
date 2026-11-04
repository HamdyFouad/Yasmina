"""Two-stage hybrid matching for tender positions and catalogue articles.

Stage 1 applies deterministic compatibility filters (material, colour, glove
size, unit). Stage 2 ranks the surviving candidates with a hybrid score:
German-aware lexical similarity plus optional embedding cosine similarity.
Candidates can be supplied in memory, or retrieved directly from PostgreSQL via
the pgvector-backed :class:`PgVectorCatalogRepository`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.models.schemas import TenderItem

__all__ = [
    "CatalogCandidate",
    "HybridMatchingService",
    "MatchResult",
    "MatchingError",
    "MatchingService",
    "PgVectorCatalogRepository",
    "candidate_from_orm",
    "cosine_similarity",
    "lexical_similarity",
]

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "und",
        "oder",
        "mit",
        "ohne",
        "für",
        "fuer",
        "je",
        "pro",
        "stk",
        "stueck",
        "stück",
        "pack",
        "packung",
        "karton",
        "inhalt",
        "ca",
        "approx",
        "the",
        "and",
        "with",
    }
)


class MatchingError(ValueError):
    """Raised for invalid matching input (dimensions, weights, limits)."""


@dataclass(frozen=True, slots=True)
class CatalogCandidate:
    """Normalized catalogue article used by the matching service."""

    id: str
    article_number: str
    description: str
    material: MaterialType | None = None
    color: StandardizedColor | None = None
    glove_size: GloveSize = GloveSize.NOT_APPLICABLE
    unit: UnitOfMeasure | None = None
    pack_size: int | None = None
    unit_price_net: Decimal | None = None
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class MatchResult:
    candidate: CatalogCandidate
    score: float
    lexical_score: float
    semantic_score: float | None
    matched_attributes: tuple[str, ...]
    reasons: tuple[str, ...]


def _normalize_text(value: str) -> str:
    folded = str(value or "").lower().translate(_FOLD)
    return " ".join(_WORD_RE.findall(folded))


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if token not in _STOPWORDS and (len(token) >= 3 or token.isdigit())
    }


def lexical_similarity(left: str, right: str) -> float:
    """Deterministic German-aware lexical score in ``[0, 1]``."""

    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0

    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    jaccard = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens and right_tokens
        else 0.0
    )
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    return round((0.55 * jaccard) + (0.45 * sequence), 6)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity in ``[-1, 1]`` for two equal-length vectors."""

    if len(left) != len(right):
        raise MatchingError(f"embedding dimensions differ: {len(left)} != {len(right)}")
    if not left:
        raise MatchingError("embeddings must not be empty")

    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(a * a for a in left))
    right_norm = math.sqrt(math.fsum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _compatible(item: TenderItem, candidate: CatalogCandidate) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if item.material is not None and candidate.material is not None and item.material != candidate.material:
        return False, [f"material mismatch: {item.material.value} != {candidate.material.value}"]
    if item.color is not None and candidate.color is not None and item.color != candidate.color:
        return False, [f"colour mismatch: {item.color.value} != {candidate.color.value}"]
    if (
        item.glove_size is not GloveSize.NOT_APPLICABLE
        and candidate.glove_size is not GloveSize.NOT_APPLICABLE
        and item.glove_size != candidate.glove_size
    ):
        return False, [f"glove size mismatch: {item.glove_size.value} != {candidate.glove_size.value}"]
    if (
        item.unit is not UnitOfMeasure.NOT_APPLICABLE
        and candidate.unit is not None
        and item.unit != candidate.unit
    ):
        return False, [f"unit mismatch: {item.unit.value} != {candidate.unit.value}"]

    if item.material is not None and candidate.material == item.material:
        reasons.append("material")
    if item.color is not None and candidate.color == item.color:
        reasons.append("colour")
    if (
        item.glove_size is not GloveSize.NOT_APPLICABLE
        and candidate.glove_size == item.glove_size
    ):
        reasons.append("glove_size")
    if item.unit is not UnitOfMeasure.NOT_APPLICABLE and candidate.unit == item.unit:
        reasons.append("unit")
    return True, reasons


def candidate_from_orm(article: Any) -> CatalogCandidate:
    """Adapt a ``CatalogArticle`` ORM row (or any attribute-compatible object)."""

    embedding = getattr(article, "embedding", None)
    return CatalogCandidate(
        id=str(getattr(article, "id", "")),
        article_number=str(getattr(article, "article_number", "")),
        description=str(getattr(article, "description", "")),
        material=getattr(article, "material", None),
        color=getattr(article, "color", None),
        glove_size=getattr(article, "glove_size", GloveSize.NOT_APPLICABLE),
        unit=getattr(article, "unit", None),
        pack_size=getattr(article, "pack_size", None),
        unit_price_net=getattr(article, "unit_price_net", None),
        embedding=tuple(float(value) for value in embedding) if embedding is not None else None,
    )


class PgVectorCatalogRepository:
    """Retrieve candidate articles from PostgreSQL with pgvector ordering.

    Stage 1 filters run in SQL (with nullable catalogue attributes treated as
    compatible). Stage 2 ranking remains in Python so lexical similarity and
    exact attribute matching stay deterministic.
    """

    def __init__(self, session: Any) -> None:
        self.session = session

    async def candidates(
        self,
        item: TenderItem,
        *,
        query_embedding: Sequence[float] | None = None,
        limit: int = 100,
    ) -> list[CatalogCandidate]:
        if limit < 1:
            raise MatchingError("candidate limit must be at least 1")

        from sqlalchemy import or_, select

        from src.models.orm import CatalogArticle

        statement = select(CatalogArticle)
        if item.material is not None:
            statement = statement.where(
                or_(CatalogArticle.material == item.material, CatalogArticle.material.is_(None))
            )
        if item.color is not None:
            statement = statement.where(
                or_(CatalogArticle.color == item.color, CatalogArticle.color.is_(None))
            )
        if item.glove_size is not GloveSize.NOT_APPLICABLE:
            statement = statement.where(
                or_(
                    CatalogArticle.glove_size == item.glove_size,
                    CatalogArticle.glove_size == GloveSize.NOT_APPLICABLE,
                )
            )
        if item.unit is not UnitOfMeasure.NOT_APPLICABLE:
            statement = statement.where(
                or_(CatalogArticle.unit == item.unit, CatalogArticle.unit.is_(None))
            )
        if query_embedding is not None:
            statement = statement.where(CatalogArticle.embedding.is_not(None)).order_by(
                CatalogArticle.embedding.cosine_distance(list(query_embedding))
            )
        statement = statement.limit(limit)

        result = await self.session.execute(statement)
        return [candidate_from_orm(row) for row in result.scalars().all()]


class HybridMatchingService:
    """Deterministic two-stage matcher.

    Stage 1 removes incompatible articles; stage 2 combines lexical similarity
    with embedding cosine similarity. If either the query or candidate has no
    embedding, the score falls back to lexical similarity only.
    """

    def __init__(
        self,
        *,
        lexical_weight: float = 0.4,
        semantic_weight: float = 0.6,
        min_score: float = 0.25,
        attribute_bonus: float = 0.03,
    ) -> None:
        if lexical_weight < 0 or semantic_weight < 0:
            raise MatchingError("weights must not be negative")
        if lexical_weight + semantic_weight <= 0:
            raise MatchingError("at least one weight must be positive")
        if not 0 <= min_score <= 1:
            raise MatchingError("min_score must be between 0 and 1")
        if attribute_bonus < 0:
            raise MatchingError("attribute_bonus must not be negative")

        total = lexical_weight + semantic_weight
        self.lexical_weight = lexical_weight / total
        self.semantic_weight = semantic_weight / total
        self.min_score = min_score
        self.attribute_bonus = attribute_bonus

    def filter_candidates(
        self,
        item: TenderItem,
        candidates: Iterable[CatalogCandidate],
    ) -> tuple[list[CatalogCandidate], list[str]]:
        """Stage 1: deterministic compatibility filtering."""

        accepted: list[CatalogCandidate] = []
        rejected: list[str] = []
        for candidate in candidates:
            compatible, reasons = _compatible(item, candidate)
            if compatible:
                accepted.append(candidate)
            else:
                rejected.append(f"{candidate.article_number}: {'; '.join(reasons)}")
        return accepted, rejected

    def rank(
        self,
        item: TenderItem,
        candidates: Iterable[CatalogCandidate],
        *,
        query_embedding: Sequence[float] | None = None,
        limit: int = 5,
    ) -> list[MatchResult]:
        """Run both stages and return the best candidates above ``min_score``."""

        if limit < 1:
            raise MatchingError("limit must be at least 1")

        accepted, _ = self.filter_candidates(item, candidates)
        results: list[MatchResult] = []
        item_text = f"{item.short_text} {item.long_text or ''}".strip()

        for candidate in accepted:
            lexical = lexical_similarity(item_text, candidate.description)

            semantic: float | None = None
            if query_embedding is not None and candidate.embedding is not None:
                semantic = cosine_similarity(query_embedding, candidate.embedding)

            if semantic is None:
                score = lexical
                reasons = ["lexical"]
            else:
                score = (self.lexical_weight * lexical) + (self.semantic_weight * semantic)
                reasons = ["lexical", "semantic"]

            _, attributes = _compatible(item, candidate)
            if attributes:
                score = min(1.0, score + self.attribute_bonus * len(attributes))
                reasons.extend(f"attr:{name}" for name in attributes)

            if score < self.min_score:
                continue

            results.append(
                MatchResult(
                    candidate=candidate,
                    score=round(score, 6),
                    lexical_score=lexical,
                    semantic_score=round(semantic, 6) if semantic is not None else None,
                    matched_attributes=tuple(attributes),
                    reasons=tuple(reasons),
                )
            )

        results.sort(key=lambda result: (-result.score, result.candidate.article_number))
        return results[:limit]

    async def match_database(
        self,
        session: Any,
        item: TenderItem,
        *,
        query_embedding: Sequence[float] | None = None,
        limit: int = 5,
        candidate_limit: int = 100,
    ) -> list[MatchResult]:
        """Retrieve candidates from PostgreSQL and rank them in one call."""

        repository = PgVectorCatalogRepository(session)
        candidates = await repository.candidates(
            item,
            query_embedding=query_embedding,
            limit=candidate_limit,
        )
        return self.rank(
            item,
            candidates,
            query_embedding=query_embedding,
            limit=limit,
        )


MatchingService = HybridMatchingService
