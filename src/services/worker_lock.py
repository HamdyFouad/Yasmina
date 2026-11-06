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
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

__all__ = [
    "DatabasePortalLock",
    "PortalLockError",
    "PortalLocker",
    "advisory_lock_key",
]

logger = logging.getLogger("yasmina.worker.lock")

_LOCK_NAMESPACE = b"yasmina:portal-lock:"


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
