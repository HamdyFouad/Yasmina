"""Batched embedding execution: batching, retries and progress reporting."""

from __future__ import annotations

import httpx
import pytest

from src.config import Settings
from src.services.embedding_pipeline import EmbeddingBatchProgress, embed_in_batches
from src.services.embedding_service import (
    EmbeddingError,
    EmbeddingRateLimitError,
    EmbeddingTransientError,
    OpenAIEmbeddingProvider,
)


class RecordingProvider:
    def __init__(self, dimensions: int = 4) -> None:
        self.dimensions = dimensions
        self.batches: list[list[str]] = []

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        self.batches.append(list(texts))
        return [[float(len(text))] * self.dimensions for text in texts]


class FlakyProvider(RecordingProvider):
    """Fails the first N calls with 429/transient errors, then succeeds."""

    def __init__(self, failures: list[Exception]) -> None:
        super().__init__()
        self.failures = list(failures)
        self.calls = 0

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return await super().embed(texts)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "jwt_secret_key": "test-embedding-jwt-secret-32-bytes-ok",
        "api_admin_password": "test-admin-password",
        "embedding_dimensions": 4,
        "embedding_api_key": "test-key",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def test_batches_are_split_and_ordered() -> None:
    provider = RecordingProvider()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    run = await embed_in_batches(
        provider,
        ["a", "bb", "ccc", "dddd", "eeeee"],
        batch_size=2,
        sleep=fake_sleep,
    )

    assert provider.batches == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert len(run.vectors) == 5
    assert run.batches == 3
    assert run.retries == 0
    assert sleeps == []


async def test_progress_callback_reports_every_batch() -> None:
    provider = RecordingProvider()
    events: list[EmbeddingBatchProgress] = []

    run = await embed_in_batches(
        provider,
        ["a", "bb", "ccc", "dddd", "eeeee"],
        batch_size=2,
        on_progress=events.append,
    )

    assert run.batches == 3
    assert [event.completed for event in events] == [2, 4, 5]
    assert [event.batch_index for event in events] == [1, 2, 3]
    assert events[-1].percent == 100.0


async def test_rate_limits_are_retried_with_backoff() -> None:
    provider = FlakyProvider(
        [
            EmbeddingRateLimitError("rate limited", retry_after=5.0),
            EmbeddingTransientError("temporary"),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    run = await embed_in_batches(
        provider,
        ["a", "b"],
        batch_size=2,
        max_retries=3,
        backoff_seconds=1.0,
        sleep=fake_sleep,
    )

    assert run.retries == 2
    assert sleeps == [5.0, 2.0]
    assert len(run.vectors) == 2


async def test_retry_budget_exhaustion_raises_contextual_error() -> None:
    provider = FlakyProvider([EmbeddingTransientError("down")] * 3)

    async def fake_sleep(delay: float) -> None:
        return None

    with pytest.raises(EmbeddingError, match="failed after 2 attempt"):
        await embed_in_batches(
            provider,
            ["a"],
            batch_size=1,
            max_retries=1,
            sleep=fake_sleep,
        )
    assert provider.calls == 2


async def test_non_retryable_errors_are_not_retried() -> None:
    provider = FlakyProvider([EmbeddingError("bad request")])

    async def fake_sleep(delay: float) -> None:
        raise AssertionError("must not retry a non-retryable error")

    with pytest.raises(EmbeddingError, match="bad request"):
        await embed_in_batches(provider, ["a"], batch_size=1, sleep=fake_sleep)
    assert provider.calls == 1


async def test_provider_vector_count_is_validated() -> None:
    class ShortProvider:
        dimensions = 4

        async def embed(self, texts):  # type: ignore[no-untyped-def]
            return [[0.0, 0.0, 0.0, 0.0]]

    with pytest.raises(EmbeddingError, match="returned 1 vector"):
        await embed_in_batches(ShortProvider(), ["a", "b"], batch_size=2)


async def test_openai_provider_maps_429_and_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "slow down"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIEmbeddingProvider(make_settings(), client=client)

    with pytest.raises(EmbeddingRateLimitError) as excinfo:
        await provider.embed(["a"])
    assert excinfo.value.retry_after == 7.0
    await client.aclose()


async def test_openai_provider_maps_5xx_to_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIEmbeddingProvider(make_settings(), client=client)

    with pytest.raises(EmbeddingTransientError):
        await provider.embed(["a"])
    await client.aclose()


async def test_openai_provider_rejects_other_errors_and_bad_dimensions() -> None:
    def bad_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(bad_request))
    provider = OpenAIEmbeddingProvider(make_settings(), client=client)
    with pytest.raises(EmbeddingError, match="400"):
        await provider.embed(["a"])
    await client.aclose()

    def wrong_dimensions(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrong_dimensions))
    provider = OpenAIEmbeddingProvider(make_settings(embedding_dimensions=4), client=client)
    with pytest.raises(EmbeddingError, match="dimension"):
        await provider.embed(["a"])
    await client.aclose()


async def test_openai_provider_orders_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [2.0, 2.0, 2.0, 2.0]},
                    {"index": 0, "embedding": [1.0, 1.0, 1.0, 1.0]},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIEmbeddingProvider(make_settings(), client=client)

    vectors = await provider.embed(["first", "second"])

    assert vectors == [[1.0] * 4, [2.0] * 4]
    await client.aclose()
