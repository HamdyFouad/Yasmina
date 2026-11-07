"""Live Redis tests: distributed buckets and middleware integration."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.rate_limit import RedisTokenBucketRateLimiter
from src.config import Settings
from src.main import create_app

REDIS_URL = os.getenv("YASMINA_TEST_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="set YASMINA_TEST_REDIS_URL to run Redis integration tests",
)


def make_settings(**overrides: object) -> Settings:
    assert REDIS_URL is not None
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "app_env": "test",
        "jwt_secret_key": "live-test-jwt-secret-with-at-least-32-bytes",
        "api_admin_password": "test-admin-password",
        "redis_url": REDIS_URL,
        "rate_limit_backend": "redis",
        "rate_limit_auth_per_minute": 2,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def _client():  # type: ignore[no-untyped-def]
    from redis.asyncio import from_url

    assert REDIS_URL is not None
    return from_url(REDIS_URL, decode_responses=True)


async def test_redis_bucket_is_shared_across_separate_clients() -> None:
    first_client = await _client()
    second_client = await _client()
    key = f"distributed:{uuid4().hex}"
    prefix = "yasmina:test:ratelimit:"
    first = RedisTokenBucketRateLimiter(first_client, clock=lambda: 1000.0, key_prefix=prefix)
    second = RedisTokenBucketRateLimiter(second_client, clock=lambda: 1000.0, key_prefix=prefix)
    try:
        assert (await first.acheck(key, limit=2, window_seconds=60)).allowed is True
        assert (await second.acheck(key, limit=2, window_seconds=60)).allowed is True
        denied = await second.acheck(key, limit=2, window_seconds=60)
        assert denied.allowed is False
        assert denied.retry_after > 0
    finally:
        await first_client.delete(f"{prefix}{key}")
        await first_client.aclose()
        await second_client.aclose()


async def test_middleware_enforces_the_redis_bucket_across_the_app() -> None:
    redis_client = await _client()
    await redis_client.flushdb()
    await redis_client.aclose()
    app = create_app(
        make_settings(),
        run_startup_checks=False,
    )
    payload = {"username": "admin", "password": "test-admin-password"}
    try:
        with TestClient(app) as client:
            first = client.post("/api/v1/auth/token", json=payload)
            second = client.post("/api/v1/auth/token", json=payload)
            third = client.post("/api/v1/auth/token", json=payload)
        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert third.json()["detail"] == "rate limit exceeded"
    finally:
        cleaner = await _client()
        await cleaner.flushdb()
        await cleaner.aclose()
