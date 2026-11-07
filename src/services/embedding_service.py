"""Semantic embeddings for catalogue seeding and query matching."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from src.config import Settings

__all__ = [
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingRateLimitError",
    "EmbeddingRetryableError",
    "EmbeddingTransientError",
    "HashingEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "SentenceTransformerEmbeddingProvider",
    "build_embedding_provider",
    "embedding_text",
]


class EmbeddingError(RuntimeError):
    """Raised when an embedding provider cannot produce a valid vector."""


class EmbeddingRetryableError(EmbeddingError):
    """Base class for transient failures the batching pipeline may retry."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class EmbeddingRateLimitError(EmbeddingRetryableError):
    """Raised when the embedding endpoint answers HTTP 429."""


class EmbeddingTransientError(EmbeddingRetryableError):
    """Raised for timeouts, transport errors and HTTP 5xx responses."""


def _retry_after_seconds(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


class EmbeddingProvider(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def embedding_text(
    *,
    description: str,
    material: str | None = None,
    color: str | None = None,
    glove_size: str | None = None,
    unit: str | None = None,
    pack_size: int | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    parts = [description]
    for value in (material, color, glove_size, unit):
        if value:
            parts.append(value)
    if pack_size is not None:
        parts.append(f"pack {pack_size}")
    if attributes:
        parts.extend(f"{key} {value}" for key, value in sorted(attributes.items()))
    return " | ".join(part.strip() for part in parts if part and part.strip())


class HashingEmbeddingProvider:
    """Deterministic offline embedding for tests and controlled dry runs.

    It is a feature-hashing bag-of-words vector, not a semantic model. The
    catalogue CLI refuses it unless ``--allow-hash-embeddings`` is supplied.
    """

    def __init__(self, dimensions: int) -> None:
        if dimensions < 8:
            raise EmbeddingError("hashing embedding dimensions must be >= 8")
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            tokens = [token.casefold() for token in text.replace("|", " ").split() if token]
            for token in tokens:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest, "big") % self.dimensions
                vector[bucket] += 1.0
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


class OpenAIEmbeddingProvider:
    """OpenAI-compatible ``/embeddings`` client (DeepSeek-compatible base URL)."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.dimensions = settings.embedding_dimensions
        self._client = client

    @property
    def api_key(self) -> str:
        secret = self.settings.embedding_api_key or self.settings.deepseek_api_key
        return secret.get_secret_value().strip() if secret is not None else ""

    @property
    def base_url(self) -> str:
        return (self.settings.embedding_base_url or self.settings.deepseek_base_url).rstrip("/")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("EMBEDDING_API_KEY or DEEPSEEK_API_KEY is required")
        payload = {
            "model": self.settings.embedding_model,
            "input": list(texts),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            if self._client is not None:
                response = await self._client.post(
                    f"{self.base_url}/embeddings",
                    json=payload,
                    headers=headers,
                )
            else:
                async with httpx.AsyncClient(timeout=self.settings.embedding_timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}/embeddings",
                        json=payload,
                        headers=headers,
                    )
        except httpx.TimeoutException as exc:
            raise EmbeddingTransientError(f"embedding request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise EmbeddingTransientError(f"embedding request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("retry-after"))
            raise EmbeddingRateLimitError(
                f"embedding endpoint rate limited (429): {response.text[:300]}",
                retry_after=retry_after,
            )
        if 500 <= response.status_code < 600:
            raise EmbeddingTransientError(
                f"embedding endpoint returned {response.status_code}: {response.text[:300]}"
            )
        if not 200 <= response.status_code < 300:
            raise EmbeddingError(f"embedding endpoint returned {response.status_code}: {response.text[:500]}")
        try:
            body = response.json()
            data = body["data"]
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("embedding endpoint returned an invalid payload") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embedding endpoint returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise EmbeddingError(
                    f"embedding dimension {len(vector)} does not match configured {self.dimensions}"
                )
        return vectors


class SentenceTransformerEmbeddingProvider:
    """Optional local BGE-M3 (or compatible sentence-transformers) provider."""

    def __init__(self, model_name: str, dimensions: int) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "sentence-transformers is not installed; install the catalogue extra "
                "or use the OpenAI-compatible embeddings provider"
            ) from exc
        self._model = SentenceTransformer(model_name)
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        result = [[float(value) for value in vector] for vector in vectors]
        for vector in result:
            if len(vector) != self.dimensions:
                raise EmbeddingError(
                    f"local embedding dimension {len(vector)} does not match {self.dimensions}"
                )
        return result


def build_embedding_provider(
    settings: Settings,
    provider: str = "auto",
) -> EmbeddingProvider:
    normalized = provider.strip().lower()
    if normalized == "auto":
        has_key = bool(
            (settings.embedding_api_key and settings.embedding_api_key.get_secret_value().strip())
            or (settings.deepseek_api_key and settings.deepseek_api_key.get_secret_value().strip())
        )
        normalized = "openai" if has_key else "hashing"
    if normalized in {"openai", "deepseek"}:
        return OpenAIEmbeddingProvider(settings)
    if normalized in {"hashing", "hash"}:
        return HashingEmbeddingProvider(settings.embedding_dimensions)
    if normalized in {"local", "sentence-transformers"}:
        return SentenceTransformerEmbeddingProvider(
            settings.embedding_model,
            settings.embedding_dimensions,
        )
    raise EmbeddingError(f"unsupported embedding provider {provider!r}")
