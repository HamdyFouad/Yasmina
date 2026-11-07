"""Redis-backed token bucket selection, fail modes and atomic accounting."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.rate_limit import (
    RedisTokenBucketRateLimiter,
    TokenBucketRateLimiter,
    build_rate_limiter,
)
from src.config import Settings


class FakeRedis:
    """Minimal async Redis double implementing the Lua token bucket contract."""

    def __init__(self) -> None:
        self.buckets: dict[str, tuple[float, float]] = {}
        self.calls: list[tuple[object, ...]] = []
        self.fail: Exception | None = None

    async def eval(self, script, numkeys, key, limit, window, now):  # type: ignore[no-untyped-def]
        self.calls.append((script, numkeys, key, limit, window, now))
        if self.fail is not None:
            raise self.fail
        capacity = float(limit)
        tokens, updated = self.buckets.get(key, (capacity, float(now)))
        refill = max(0.0, float(now) - updated) * (capacity / float(window))
        tokens = min(capacity, tokens + refill)
        if tokens >= 1.0:
            tokens -= 1.0
            allowed, retry_ms = 1, 0
        else:
            allowed, retry_ms = 0, int((1.0 - tokens) * (float(window) / capacity) * 1000)
        self.buckets[key] = (tokens, float(now))
        return [allowed, int(tokens), retry_ms]

    async def aclose(self) -> None:
        return None


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "jwt_secret_key": "test-rate-limit-jwt-secret-32-bytes-ok",
        "api_admin_password": "test-admin-password",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_backend_selection_defaults_to_memory_without_redis_url() -> None:
    assert isinstance(build_rate_limiter(make_settings()), TokenBucketRateLimiter)


def test_redis_url_switches_the_backend_to_redis() -> None:
    limiter = build_rate_limiter(make_settings(redis_url="redis://localhost:6379/0"))

    assert isinstance(limiter, RedisTokenBucketRateLimiter)


def test_explicit_redis_backend_requires_a_url() -> None:
    with pytest.raises(ValidationError, match="requires REDIS_URL"):
        make_settings(rate_limit_backend="redis")


async def test_redis_bucket_is_shared_between_instances() -> None:
    client = FakeRedis()
    first = RedisTokenBucketRateLimiter(client, clock=lambda: 1000.0)
    second = RedisTokenBucketRateLimiter(client, clock=lambda: 1000.0)

    assert (await first.acheck("auth:1.2.3.4", limit=2, window_seconds=60)).allowed is True
    assert (await second.acheck("auth:1.2.3.4", limit=2, window_seconds=60)).allowed is True
    denied = await first.acheck("auth:1.2.3.4", limit=2, window_seconds=60)

    assert denied.allowed is False
    assert denied.remaining == 0
    assert denied.retry_after > 0
    assert client.calls[0][2] == "yasmina:ratelimit:auth:1.2.3.4"


async def test_redis_bucket_refills_after_the_window() -> None:
    now = [1000.0]
    client = FakeRedis()
    limiter = RedisTokenBucketRateLimiter(client, clock=lambda: now[0])

    await limiter.acheck("api", limit=1, window_seconds=60)
    assert (await limiter.acheck("api", limit=1, window_seconds=60)).allowed is False
    now[0] += 61.0
    assert (await limiter.acheck("api", limit=1, window_seconds=60)).allowed is True


async def test_redis_outage_fails_open_by_default() -> None:
    client = FakeRedis()
    client.fail = ConnectionError("redis down")
    limiter = RedisTokenBucketRateLimiter(client, fail_open=True)

    decision = await limiter.acheck("auth:1.2.3.4", limit=2, window_seconds=60)

    assert decision.allowed is True
    assert limiter.last_error is not None


async def test_redis_outage_can_fail_closed() -> None:
    client = FakeRedis()
    client.fail = ConnectionError("redis down")
    limiter = RedisTokenBucketRateLimiter(client, fail_open=False)

    decision = await limiter.acheck("auth:1.2.3.4", limit=2, window_seconds=60)

    assert decision.allowed is False
    assert decision.retry_after == 1.0


async def test_close_forwards_to_the_client() -> None:
    client = FakeRedis()
    limiter = RedisTokenBucketRateLimiter(client)

    await limiter.aclose()
