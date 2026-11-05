"""Async SQLAlchemy engine/session factory for API and services."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import Settings

__all__ = ["dispose_engine", "get_engine", "get_session_factory"]

_ENGINES: dict[tuple[str, bool, int], AsyncEngine] = {}


def get_engine(settings: Settings) -> AsyncEngine:
    assert settings.database_url is not None
    key = (settings.database_url, settings.database_echo, settings.database_pool_size)
    engine = _ENGINES.get(key)
    if engine is None:
        engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_size=settings.database_pool_size,
            pool_pre_ping=True,
        )
        _ENGINES[key] = engine
    return engine


def get_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def dispose_engine() -> None:
    """Dispose all cached engines (called on FastAPI shutdown/tests)."""

    for engine in list(_ENGINES.values()):
        await engine.dispose()
    _ENGINES.clear()
