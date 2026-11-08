"""PostgreSQL advisory locks that serialise portal scrapes across workers.

Each portal maps to a deterministic signed 64-bit key. A worker checks out a
dedicated database connection, takes ``pg_try_advisory_lock(key)`` and keeps
that connection open for the duration of the scrape. The lock is released
explicitly in a ``finally`` block; if the process crashes, PostgreSQL releases
it when the connection drops.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from src.config import Settings

__all__ = [
    "DatabasePortalLock",
    "DomainLocker",
    "LocalDomainLock",
    "PortalLockError",
    "PortalLocker",
    "RedisDomainLock",
    "advisory_lock_key",
    "build_domain_locker",
]

logger = logging.getLogger("yasmina.worker.lock")

_LOCK_NAMESPACE = b"yasmina:portal-lock:"
_DOMAIN_LOCK_PREFIX = "yasmina:scrape:domain-lock:"
_RELEASE_DOMAIN_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class PortalLockError(RuntimeError):
    """Raised when an advisory lock cannot be acquired or released."""


def advisory_lock_key(portal: str) -> int:
    """Return a stable signed 64-bit PostgreSQL advisory lock key."""

    digest = hashlib.blake2b(
        _LOCK_NAMESPACE + portal.strip().lower().encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


class PortalLocker(Protocol):
    async def acquire(self, portal: str) -> bool: ...

    async def release(self, portal: str) -> None: ...

    async def release_all(self) -> None: ...


class DomainLocker(Protocol):
    """Exclusive scrape lock for one HTTP domain."""

    async def acquire(self, domain: str) -> bool: ...

    async def release(self, domain: str) -> None: ...

    async def release_all(self) -> None: ...

    async def aclose(self) -> None: ...


class LocalDomainLock:
    """Process-local fallback used when Redis is not configured."""

    def __init__(self) -> None:
        self._held: set[str] = set()

    async def acquire(self, domain: str) -> bool:
        domain = domain.strip().lower()
        if domain in self._held:
            return False
        self._held.add(domain)
        return True

    async def release(self, domain: str) -> None:
        self._held.discard(domain.strip().lower())

    async def release_all(self) -> None:
        self._held.clear()

    async def aclose(self) -> None:
        return None


class RedisDomainLock:
    """Distributed SET NX/PX lock so only one worker hits a domain at a time."""

    def __init__(self, client: Any, *, ttl_seconds: int = 1800) -> None:
        self._client = client
        self._ttl_ms = max(1, ttl_seconds) * 1000
        self._tokens: dict[str, str] = {}

    async def acquire(self, domain: str) -> bool:
        domain = domain.strip().lower()
        if domain in self._tokens:
            return False
        token = uuid4().hex
        try:
            acquired = await self._client.set(
                f"{_DOMAIN_LOCK_PREFIX}{domain}",
                token,
                nx=True,
                px=self._ttl_ms,
            )
        except Exception as exc:
            logger.warning("redis domain lock unavailable for %s: %s", domain, exc)
            return False
        if not acquired:
            return False
        self._tokens[domain] = token
        return True

    async def release(self, domain: str) -> None:
        domain = domain.strip().lower()
        token = self._tokens.pop(domain, None)
        if token is None:
            return
        try:
            await self._client.eval(
                _RELEASE_DOMAIN_LUA,
                1,
                f"{_DOMAIN_LOCK_PREFIX}{domain}",
                token,
            )
        except Exception as exc:  # pragma: no cover - lock expires after TTL anyway
            logger.warning("could not release redis domain lock for %s: %s", domain, exc)

    async def release_all(self) -> None:
        for domain in list(self._tokens):
            await self.release(domain)

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def build_domain_locker(settings: Settings) -> DomainLocker:
    """Build a distributed domain lock when Redis is configured."""

    if not settings.redis_url:
        return LocalDomainLock()
    from redis.asyncio import from_url

    client = from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
    )
    return RedisDomainLock(client)


class DatabasePortalLock:
    """Connection-scoped advisory locks held for one scrape at a time."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._held: dict[str, tuple[AsyncConnection, int]] = {}

    async def acquire(self, portal: str) -> bool:
        if portal in self._held:
            return False
        key = advisory_lock_key(portal)
        connection: AsyncConnection | None = None
        try:
            connection = await self._engine.connect()
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": key},
            )
            acquired = bool(result.scalar_one())
            await connection.commit()
        except Exception as exc:
            if connection is not None:
                await connection.close()
            raise PortalLockError(f"cannot acquire portal lock for {portal!r}: {exc}") from exc

        if not acquired:
            await connection.close()
            return False
        self._held[portal] = (connection, key)
        logger.info("acquired portal lock portal=%s key=%s", portal, key)
        return True

    async def release(self, portal: str) -> None:
        held = self._held.pop(portal, None)
        if held is None:
            return
        connection, key = held
        try:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await connection.commit()
        except Exception as exc:  # pragma: no cover - connection loss closes the lock anyway
            logger.warning("could not explicitly release portal lock portal=%s: %s", portal, exc)
        finally:
            await connection.close()
        logger.info("released portal lock portal=%s key=%s", portal, key)

    async def release_all(self) -> None:
        for portal in list(self._held):
            await self.release(portal)
