"""Per-domain rate limiting and distributed lock tests."""

from __future__ import annotations

from typing import Any

from src.config import Settings
from src.scrapers.domain_limits import (
    InMemoryDomainRateLimiter,
    RedisDomainRateLimiter,
    build_domain_rate_limiter,
)
from src.services.worker_lock import (
    LocalDomainLock,
    RedisDomainLock,
    build_domain_locker,
)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "database_url": None,
        "scraper_request_delay_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


async def test_in_memory_limiter_spaces_each_domain_independently() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    limiter = InMemoryDomainRateLimiter(clock=lambda: now[0], sleep=sleep)

    await limiter.wait("portal-a.test", 2.0)
    await limiter.wait("portal-b.test", 2.0)
    await limiter.wait("portal-a.test", 2.0)

    assert sleeps == [2.0]
    assert now[0] == 2.0


async def test_in_memory_limiter_uses_robots_crawl_delay() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    limiter = InMemoryDomainRateLimiter(clock=lambda: now[0], sleep=sleep)
    await limiter.wait("portal.test", 5.0)
    await limiter.wait("portal.test", 5.0)
    assert sleeps == [5.0]


class FakeRedis:
    def __init__(self, wait_ms: int = 0, lock_acquired: bool = True) -> None:
        self.wait_ms = wait_ms
        self.lock_acquired = lock_acquired
        self.eval_calls: list[tuple[Any, ...]] = []
        self.set_calls: list[tuple[Any, ...]] = []
        self.deleted: list[str] = []
        self.aclose_called = False

    async def eval(self, script: str, numkeys: int, *args: Any) -> int | bool:
        self.eval_calls.append((script, numkeys, *args))
        if "DEL" in script:
            key = args[0]
            self.deleted.append(str(key))
            return 1
        return self.wait_ms

    async def set(self, key: str, token: str, **kwargs: Any) -> bool | None:
        self.set_calls.append((key, token, kwargs))
        return self.lock_acquired

    async def aclose(self) -> None:
        self.aclose_called = True


async def test_redis_limiter_sleeps_for_returned_slot_and_scopes_by_domain() -> None:
    client = FakeRedis(wait_ms=1500)
    sleeps: list[float] = []
    limiter = RedisDomainRateLimiter(client, sleep=lambda delay: _record(sleeps, delay))

    await limiter.wait("portal-a.test", 1.0)
    await limiter.wait("portal-b.test", 1.0)

    assert sleeps == [1.5, 1.5]
    keys = [call[2] for call in client.eval_calls]
    assert keys == [
        "yasmina:scrape:domain:portal-a.test",
        "yasmina:scrape:domain:portal-b.test",
    ]
    await limiter.aclose()
    assert client.aclose_called is True


async def _record(target: list[float], delay: float) -> None:
    target.append(delay)


def test_builders_fall_back_to_local_without_redis() -> None:
    assert isinstance(build_domain_rate_limiter(make_settings()), InMemoryDomainRateLimiter)
    assert isinstance(build_domain_locker(make_settings()), LocalDomainLock)


async def test_local_domain_lock_is_exclusive_per_domain() -> None:
    lock = LocalDomainLock()
    assert await lock.acquire("a.test") is True
    assert await lock.acquire("a.test") is False
    assert await lock.acquire("b.test") is True
    await lock.release("a.test")
    assert await lock.acquire("a.test") is True
    await lock.release_all()
    await lock.aclose()
    assert await lock.acquire("a.test") is True


async def test_redis_domain_lock_uses_nx_and_compare_and_delete() -> None:
    client = FakeRedis()
    lock = RedisDomainLock(client)

    assert await lock.acquire("portal.test") is True
    assert await lock.acquire("portal.test") is False
    assert len(client.set_calls) == 1
    key, token, kwargs = client.set_calls[0]
    assert key == "yasmina:scrape:domain-lock:portal.test"
    assert kwargs == {"nx": True, "px": 1_800_000}
    await lock.release("portal.test")
    assert client.deleted == ["yasmina:scrape:domain-lock:portal.test"]
    assert await lock.acquire("portal.test") is True
    assert token
    await lock.release_all()
    await lock.aclose()


async def test_redis_domain_lock_fails_closed_on_redis_error() -> None:
    class BrokenRedis(FakeRedis):
        async def set(self, key: str, token: str, **kwargs: Any) -> bool | None:
            raise RuntimeError("redis down")

    lock = RedisDomainLock(BrokenRedis())
    assert await lock.acquire("portal.test") is False
