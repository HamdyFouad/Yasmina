"""n8n RFQ dispatch and supplier-quote callback endpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
    get_audit_service,
    get_integration_repository,
    get_n8n_client,
    get_session,
    get_supplier_quote_repository,
    get_tender_repository,
    require_roles,
)
from src.api.schemas import (
    RFQDispatchPayload,
    SupplierQuoteCallbackPayload,
    parse_supplier_quotes,
)
from src.api.security import Principal, Role
from src.integrations.n8n_client import (
    N8nClient,
    N8nConfigurationError,
    N8nError,
    N8nSignatureError,
    build_rfq_payload,
)
from src.models.enums import AuditEventType
from src.repositories.integration_repository import IntegrationRepository
from src.repositories.supplier_quote_repository import (
    SupplierQuoteRepository,
    SupplierQuoteRepositoryError,
)
from src.repositories.tender_repository import TenderRepository
from src.services.audit_service import AuditService, AuditServiceError

__all__ = ["router"]

logger = logging.getLogger("yasmina.integrations")
router = APIRouter(prefix="/integrations", tags=["integrations"])

_DISPATCH_OPERATION = "n8n.rfq.dispatch"
_QUOTE_INGEST_OPERATION = "n8n.supplier_quote.ingest"
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _require_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )
    key = value.strip()
    if not _KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be 1-128 characters of letters, digits, '.', '_', ':' or '-'",
        )
    return key


def _request_hash(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _reject_key_reuse(
    *,
    operation: str,
    request_hash: str,
    record_operation: str,
    record_hash: str,
) -> None:
    if record_operation != operation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key is already used for another operation",
        )
    if record_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used with a different payload",
        )


@router.post("/rfq/dispatch")
async def dispatch_rfq(
    payload: RFQDispatchPayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_roles(Role.SALES, Role.ADMIN)),
    tender_repository: TenderRepository = Depends(get_tender_repository),
    repository: IntegrationRepository = Depends(get_integration_repository),
    n8n: N8nClient = Depends(get_n8n_client),
    audit: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Send a signed RFQ envelope to n8n, replaying completed idempotent calls."""

    key = _require_idempotency_key(idempotency_key)
    if not n8n.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="n8n webhook URL or HMAC secret is not configured",
        )

    stored = await tender_repository.get(payload.tender_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tender not found")

    request_hash = _request_hash(payload.model_dump(mode="json"))
    record, created = await repository.reserve(
        key=key,
        operation=_DISPATCH_OPERATION,
        request_hash=request_hash,
    )
    if not created:
        _reject_key_reuse(
            operation=_DISPATCH_OPERATION,
            request_hash=request_hash,
            record_operation=record.operation,
            record_hash=record.request_hash,
        )
        if record.status == "succeeded":
            cached = dict(record.response or {})
            cached["replayed"] = True
            return cached
        if record.status == "failed":
            await repository.reopen(key)
            await session.commit()
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="an identical request is already in progress",
            )
    else:
        # Commit the reservation before the network call so concurrent workers
        # see the key as pending.
        await session.commit()

    event_id = str(uuid4())
    body = build_rfq_payload(
        event_id=event_id,
        tender=stored.tender,
        suppliers=[supplier.model_dump(mode="json") for supplier in payload.suppliers],
        callback_url=n8n.callback_url,
        idempotency_key=key,
        response_deadline=payload.response_deadline,
        notes=payload.notes,
    )
    try:
        result = await n8n.dispatch_rfq(body, idempotency_key=key)
    except N8nError as exc:
        await repository.mark_failed(key, str(exc))
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"n8n RFQ dispatch failed: {exc}",
        ) from exc

    response: dict[str, Any] = {
        "status": "dispatched",
        "event_id": event_id,
        "idempotency_key": key,
        "webhook_status": result.status_code,
        "n8n_response": result.response,
        "replayed": False,
    }
    await repository.mark_succeeded(key, response)
    await session.commit()

    try:
        await audit.append(
            event_type=AuditEventType.RFQ_DISPATCHED,
            actor=principal.sub,
            entity_type="tender",
            entity_id=str(stored.tender.id),
            payload={
                "idempotency_key": key,
                "event_id": event_id,
                "supplier_count": len(payload.suppliers),
                "tender_item_count": len(stored.tender.items),
            },
        )
    except AuditServiceError as exc:  # the webhook is already gone; do not retry it here
        logger.warning("RFQ %s dispatched but audit append failed: %s", event_id, exc)
        response["audit_recorded"] = False
    else:
        response["audit_recorded"] = True
    return response


@router.post("/n8n/supplier-quotes")
async def ingest_supplier_quotes(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    n8n: N8nClient = Depends(get_n8n_client),
    repository: IntegrationRepository = Depends(get_integration_repository),
    quotes: SupplierQuoteRepository = Depends(get_supplier_quote_repository),
    audit: AuditService = Depends(get_audit_service),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Verify an n8n callback, then persist supplier quotes idempotently."""

    raw = await request.body()
    try:
        n8n.verify_callback(raw, request.headers)
    except N8nConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except N8nSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    key = _require_idempotency_key(idempotency_key)
    try:
        payload = SupplierQuoteCallbackPayload.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False),
        ) from exc

    try:
        parsed_quotes = parse_supplier_quotes(payload.quotes)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False),
        ) from exc

    request_hash = hashlib.sha256(raw).hexdigest()
    record, created = await repository.reserve(
        key=key,
        operation=_QUOTE_INGEST_OPERATION,
        request_hash=request_hash,
    )
    if not created:
        _reject_key_reuse(
            operation=_QUOTE_INGEST_OPERATION,
            request_hash=request_hash,
            record_operation=record.operation,
            record_hash=record.request_hash,
        )
        if record.status == "succeeded":
            cached = dict(record.response or {})
            cached["duplicate"] = True
            return cached
        if record.status == "failed":
            await repository.reopen(key)
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="an identical callback is already in progress",
            )

    stored_quotes = []
    try:
        for quote in parsed_quotes:
            stored_quotes.append(await quotes.save(quote))
        await session.flush()
        response: dict[str, Any] = {
            "status": "received",
            "event_id": payload.event_id,
            "idempotency_key": key,
            "quote_ids": [str(stored.quote_id) for stored in stored_quotes],
            "created": [stored.created for stored in stored_quotes],
            "duplicate": False,
        }
        await repository.mark_succeeded(key, response)
        await audit.append(
            event_type=AuditEventType.SUPPLIER_QUOTE_INGESTED,
            actor="n8n",
            entity_type="supplier_quote",
            entity_id=str(stored_quotes[0].quote_id),
            payload={
                "idempotency_key": key,
                "event_id": payload.event_id,
                "quote_ids": response["quote_ids"],
                "created": response["created"],
            },
            commit=False,
        )
        await session.commit()
    except (SupplierQuoteRepositoryError, IntegrityError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"supplier quote ingestion failed: {exc}",
        ) from exc
    except AuditServiceError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"audit log unavailable: {exc}",
        ) from exc

    return response
