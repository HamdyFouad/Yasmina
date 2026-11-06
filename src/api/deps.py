"""FastAPI dependencies for authentication, database sessions and storage."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.security import AuthError, Principal, Role, decode_access_token
from src.agents.checkpoint import SqlAlchemyCheckpointSaver
from src.config import Settings, get_settings
from src.db import get_session_factory
from src.integrations.clamav_client import ClamAVScanner
from src.integrations.erpnext_client import ERPNextClient
from src.integrations.minio_client import MinioObjectStorage
from src.integrations.n8n_client import N8nClient
from src.repositories.integration_repository import IntegrationRepository
from src.repositories.ingestion_repository import TenderIngestionJobRepository
from src.repositories.invoice_repository import InvoiceRepository
from src.repositories.offer_repository import OfferRepository
from src.repositories.supplier_quote_repository import SupplierQuoteRepository
from src.repositories.tender_repository import TenderRepository
from src.repositories.user_repository import (
    RefreshTokenRepository,
    TokenBlocklistRepository,
    UserRepository,
)
from src.repositories.worker_run_repository import WorkerRunRepository
from src.services.audit_service import AuditService
from src.services.worker_metrics import WorkerHealthService

__all__ = [
    "bearer_scheme",
    "get_app_settings",
    "get_audit_service",
    "get_clamav_scanner",
    "get_erpnext_client",
    "get_integration_repository",
    "get_invoice_repository",
    "get_n8n_client",
    "get_object_storage",
    "get_offer_repository",
    "get_principal",
    "get_refresh_token_repository",
    "get_session",
    "get_supplier_quote_repository",
    "get_tender_repository",
    "get_token_blocklist_repository",
    "get_user_repository",
    "get_worker_health_service",
    "get_workflow_checkpointer",
    "require_roles",
]

bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    settings = get_app_settings(request)
    factory = get_session_factory(settings)
    async with factory() as session:
        yield session


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        principal = decode_access_token(credentials.credentials, settings)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if await TokenBlocklistRepository(session).is_revoked(principal.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_roles(*required: Role):  # type: ignore[no-untyped-def]
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_any(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of roles: {', '.join(role.value for role in required)}",
            )
        return principal

    return dependency


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


def get_audit_service(
    session: AsyncSession = Depends(get_session),
) -> AuditService:
    return AuditService(session)


def get_object_storage(
    settings: Settings = Depends(get_app_settings),
) -> MinioObjectStorage:
    return MinioObjectStorage(settings)


def get_erpnext_client(
    settings: Settings = Depends(get_app_settings),
) -> ERPNextClient:
    return ERPNextClient(settings)


def get_n8n_client(
    settings: Settings = Depends(get_app_settings),
) -> N8nClient:
    return N8nClient(settings)


def get_integration_repository(
    session: AsyncSession = Depends(get_session),
) -> IntegrationRepository:
    return IntegrationRepository(session)


def get_supplier_quote_repository(
    session: AsyncSession = Depends(get_session),
) -> SupplierQuoteRepository:
    return SupplierQuoteRepository(session)


def get_offer_repository(
    session: AsyncSession = Depends(get_session),
) -> OfferRepository:
    return OfferRepository(session)


def get_invoice_repository(
    session: AsyncSession = Depends(get_session),
) -> InvoiceRepository:
    return InvoiceRepository(session)


def get_workflow_checkpointer(
    settings: Settings = Depends(get_app_settings),
) -> SqlAlchemyCheckpointSaver:
    return SqlAlchemyCheckpointSaver(get_session_factory(settings))


def get_worker_health_service(
    session: AsyncSession = Depends(get_session),
) -> WorkerHealthService:
    return WorkerHealthService(
        WorkerRunRepository(session),
        TenderIngestionJobRepository(session),
    )


def get_user_repository(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> UserRepository:
    return UserRepository(session, password_min_length=settings.password_min_length)


def get_refresh_token_repository(
    session: AsyncSession = Depends(get_session),
) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


def get_token_blocklist_repository(
    session: AsyncSession = Depends(get_session),
) -> TokenBlocklistRepository:
    return TokenBlocklistRepository(session)
