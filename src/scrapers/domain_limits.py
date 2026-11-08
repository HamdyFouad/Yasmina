"""Per-domain politeness primitives for concurrent portal workers.

HTML scraping must never rely on one global delay: 100 portals share the
internet but not the same host, and several portals can live on one domain. A
Redis-backed spacing limiter reserves a slot per domain (shared by every worker
process), while an in-memory limiter provides the same semantics for tests and
single-process development.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from src.config import Settings

__all__ = [
    "DomainRateLimiter",
    "InMemoryDomainRateLimiter",
    "RedisDomainRateLimiter",
    "build_domain_rate_limiter",
]

logger = logging.getLogger("yasmina.scraper.rate_limit")

REDIS_DOMAIN_SPACING_LUA = """
local now = tonumber(ARGV[1])
local delay = tonumber(ARGV[2])
local next_at = tonumber(redis.call('GET', KEYS[1]) or '0')
if next_at < now then
  next_at = now
end
local wait = next_at - now
local ttl = math.ceil((wait + delay + 60) * 1000)
redis.call('SET', KEYS[1], tostring(next_at + delay), 'PX', ttl)
return math.floor(wait * 1000)
"""


class DomainRateLimiter(Protocol):
    async def wait(self, domain: str, delay_seconds: float) -> None: ...

    async def aclose(self) -> None: ...


class InMemoryDomainRateLimiter:
    """Reserve the next free request slot per domain inside one process."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._next_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, domain: str, delay_seconds: float) -> None:
        delay = max(0.0, delay_seconds)
        async with self._lock:
            now = self._clock()
            next_at = max(now, self._next_at.get(domain, now))
            self._next_at[domain] = next_at + delay
            wait = next_at - now
        if wait > 0:
            await self._sleep(wait)

    async def aclose(self) -> None:
        return None


class RedisDomainRateLimiter:
    """Atomic per-domain request spacing shared across workers."""

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        key_prefix: str = "yasmina:scrape:domain:",
    ) -> None:
        self._client = client
        self._clock = clock or time.time
        self._sleep = sleep or asyncio.sleep
        self._key_prefix = key_prefix
        self._fallback = InMemoryDomainRateLimiter(clock=clock, sleep=sleep)

    async def wait(self, domain: str, delay_seconds: float) -> None:
        delay = max(0.0, delay_seconds)
        if delay <= 0:
            return
        try:
            wait_ms = await self._client.eval(
                REDIS_DOMAIN_SPACING_LUA,
                1,
                f"{self._key_prefix}{domain}",
                self._clock(),
                delay,
            )
        except Exception as exc:  # pragma: no cover - exercised with fake clients
            logger.warning("redis domain limiter unavailable; using local fallback: %s", exc)
            await self._fallback.wait(domain, delay)
            return
        wait = max(0.0, float(wait_ms) / 1000.0)
        if wait > 0:
            await self._sleep(wait)

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def build_domain_rate_limiter(settings: Settings) -> DomainRateLimiter:
    """Use Redis when configured, otherwise keep per-process spacing."""

    if not settings.redis_url:
        return InMemoryDomainRateLimiter()
    from redis.asyncio import from_url

    client = from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
    )
    return RedisDomainRateLimiter(client)
