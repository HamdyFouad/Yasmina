"""Async SQLAlchemy engine/session factory for API and services."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import Settings

__all__ = ["get_engine", "get_session_factory"]


@lru_cache(maxsize=4)
def _build_engine(database_url: str, echo: bool, pool_size: int) -> AsyncEngine:
    return create_async_engine(
        database_url,
        echo=echo,
        pool_size=pool_size,
        pool_pre_ping=True,
    )


def get_engine(settings: Settings) -> AsyncEngine:
    assert settings.database_url is not None
    return _build_engine(settings.database_url, settings.database_echo, settings.database_pool_size)


def get_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)
