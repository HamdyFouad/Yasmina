"""Batched embedding execution with retries and progress reporting.

The seed and reindex commands share this pipeline: it splits texts into batches,
retries rate-limited or transient batches with exponential backoff (honouring a
``Retry-After`` hint), and reports progress after every completed batch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from time import perf_counter

from src.services.embedding_service import (
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingRetryableError,
)

__all__ = [
    "EmbeddingBatchProgress",
    "EmbeddingRun",
    "embed_in_batches",
]


@dataclass(frozen=True, slots=True)
class EmbeddingBatchProgress:
    completed: int
    total: int
    batch_index: int
    batch_count: int

    @property
    def percent(self) -> float:
        return (self.completed / self.total * 100.0) if self.total else 100.0


@dataclass(frozen=True, slots=True)
class EmbeddingRun:
    vectors: list[list[float]]
    batches: int
    retries: int
    elapsed_seconds: float


ProgressCallback = Callable[[EmbeddingBatchProgress], None]


def _retry_delay(
    exc: EmbeddingRetryableError,
    *,
    attempt: int,
    backoff: float,
    max_backoff: float,
) -> float:
    if exc.retry_after is not None:
        return float(max(0.0, min(float(exc.retry_after), max_backoff)))
    return float(max(0.0, min(max_backoff, backoff * float(2**attempt))))


async def embed_in_batches(
    provider: EmbeddingProvider,
    texts: Sequence[str],
    *,
    batch_size: int,
    max_retries: int = 4,
    backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    on_progress: ProgressCallback | None = None,
) -> EmbeddingRun:
    """Embed all texts in order, retrying rate-limited batches."""

    items = list(texts)
    size = max(1, int(batch_size))
    attempts_allowed = max(0, int(max_retries))
    sleeper = sleep or asyncio.sleep
    started = perf_counter()
    vectors: list[list[float]] = []
    retries = 0
    batch_count = (len(items) + size - 1) // size if items else 0
    completed = 0

    for batch_index in range(batch_count):
        batch = items[batch_index * size : (batch_index + 1) * size]
        attempt = 0
        while True:
            try:
                batch_vectors = await provider.embed(batch)
                break
            except EmbeddingRetryableError as exc:
                if attempt >= attempts_allowed:
                    raise EmbeddingError(
                        f"embedding batch {batch_index + 1}/{batch_count} failed after "
                        f"{attempt + 1} attempt(s): {exc}"
                    ) from exc
                await sleeper(
                    _retry_delay(
                        exc,
                        attempt=attempt,
                        backoff=backoff_seconds,
                        max_backoff=max_backoff_seconds,
                    )
                )
                attempt += 1
                retries += 1
        if len(batch_vectors) != len(batch):
            raise EmbeddingError(
                f"embedding provider returned {len(batch_vectors)} vector(s) for {len(batch)} input(s)"
            )
        vectors.extend(batch_vectors)
        completed += len(batch)
        if on_progress is not None:
            on_progress(
                EmbeddingBatchProgress(
                    completed=completed,
                    total=len(items),
                    batch_index=batch_index + 1,
                    batch_count=batch_count,
                )
            )

    return EmbeddingRun(
        vectors=vectors,
        batches=batch_count,
        retries=retries,
        elapsed_seconds=perf_counter() - started,
    )
