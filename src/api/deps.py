"""FastAPI dependencies for authentication, database sessions and storage."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.security import AuthError, Principal, Role, decode_access_token
from src.config import Settings, get_settings
from src.db import get_session_factory
from src.integrations.clamav_client import ClamAVScanner
from src.repositories.tender_repository import TenderRepository

__all__ = [
    "bearer_scheme",
    "get_app_settings",
    "get_clamav_scanner",
    "get_principal",
    "get_session",
    "get_tender_repository",
    "require_roles",
]

bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_app_settings),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(credentials.credentials, settings)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_roles(*required: Role):  # type: ignore[no-untyped-def]
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_any(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of roles: {', '.join(role.value for role in required)}",
            )
        return principal

    return dependency


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    settings = get_app_settings(request)
    factory = get_session_factory(settings)
    async with factory() as session:
        yield session


def get_tender_repository(
    session: AsyncSession = Depends(get_session),
) -> TenderRepository:
    return TenderRepository(session)


def get_clamav_scanner(
    settings: Settings = Depends(get_app_settings),
) -> ClamAVScanner:
    return ClamAVScanner(
        settings.clamav_host,
        settings.clamav_port,
        timeout=settings.clamav_timeout_seconds,
    )
