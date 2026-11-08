"""Background ERPNext quotation sync using the durable outbox."""

from __future__ import annotations

import logging
import os
import socket
from asyncio import Event
from typing import Any

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.integrations.erpnext_client import ERPNextClient, ERPNextError, ERPNextNotConfigured
from src.models.enums import AuditEventType
from src.repositories.offer_repository import OfferRepository, OfferRepositoryError
from src.repositories.quotation_sync_repository import (
    QuotationSyncJobRepository,
    QuotationSyncJobView,
    QuotationSyncRepositoryError,
)
from src.services.audit_service import AuditService

__all__ = ["process_quotation_sync_jobs_once", "quotation_sync_backoff"]

logger = logging.getLogger("yasmina.quotation_sync")


def quotation_sync_backoff(attempts: int, base_seconds: int) -> int:
    """Exponential backoff for an outbox attempt (capped at one hour)."""

    exponent = max(0, attempts - 1)
    return int(min(3600, max(1, base_seconds) * (2**exponent)))


async def process_quotation_sync_jobs_once(
    *,
    settings: Settings,
    limit: int | None = None,
    worker_id: str | None = None,
    stop_event: Event | None = None,
) -> list[dict[str, Any]]:
    """Claim and process queued ERPNext quotation jobs.

    ERPNext calls happen outside the approval request. Each failure is recorded
    on the job row and retried with exponential backoff; the offer is only
    updated after the quotation has been created and the audit event is staged
    in the same transaction.
    """

    client = ERPNextClient(settings)
    if not client.configured:
        await client.aclose()
        logger.info("ERPNext is not configured; quotation sync queue left untouched")
        return []

    active_worker = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    batch_size = max(1, limit or settings.quotation_sync_batch_size)
    factory = get_session_factory(settings)
    summaries: list[dict[str, Any]] = []
    try:
        async with factory() as session:
            jobs = QuotationSyncJobRepository(session)
            offers = OfferRepository(session)
            audit = AuditService(session)
            claimed = await jobs.claim_pending(limit=batch_size, worker_id=active_worker)
            for job in claimed:
                if stop_event is not None and stop_event.is_set():
                    await jobs.release(job.id)
                    summaries.append(
                        {"job_id": str(job.id), "offer_number": job.offer_number, "status": "cancelled"}
                    )
                    continue
                summary = await _process_one(
                    job,
                    jobs=jobs,
                    offers=offers,
                    audit=audit,
                    client=client,
                    settings=settings,
                )
                summaries.append(summary)
    finally:
        await client.aclose()
        await dispose_engine()
    return summaries


async def _process_one(
    job: QuotationSyncJobView,
    *,
    jobs: QuotationSyncJobRepository,
    offers: OfferRepository,
    audit: AuditService,
    client: ERPNextClient,
    settings: Settings,
) -> dict[str, Any]:
    stored = await offers.get(job.offer_id)
    if stored is None:
        await jobs.skip(job.id, "offer row disappeared")
        return {"job_id": str(job.id), "offer_number": job.offer_number, "status": "skipped"}

    try:
        if stored.offer.erpnext_quotation_name:
            await jobs.complete(job.id, quotation_name=stored.offer.erpnext_quotation_name)
            return {
                "job_id": str(job.id),
                "offer_number": job.offer_number,
                "status": "succeeded",
                "quotation_name": stored.offer.erpnext_quotation_name,
            }

        quotation = await client.create_quotation(stored.offer)
        await audit.append(
            event_type=AuditEventType.ERP_QUOTATION_CREATED,
            actor="quotation_sync_worker",
            entity_type="customer_offer",
            entity_id=str(stored.offer.id),
            payload={
                "offer_number": stored.offer.offer_number,
                "quotation_name": quotation.name,
                "created": quotation.created,
                "approved_by": stored.offer.approved_by,
                "job_id": str(job.id),
                "attempt": job.attempts,
            },
            commit=False,
        )
        updated = stored.offer.model_copy(update={"erpnext_quotation_name": quotation.name})
        await offers.save(updated, commit=False)
        await jobs.complete(job.id, quotation_name=quotation.name)
        return {
            "job_id": str(job.id),
            "offer_number": job.offer_number,
            "status": "succeeded",
            "quotation_name": quotation.name,
        }
    except ERPNextNotConfigured as exc:
        await jobs.skip(job.id, f"ERPNext is not configured: {exc}")
        return {"job_id": str(job.id), "offer_number": job.offer_number, "status": "skipped"}
    except (ERPNextError, OfferRepositoryError, QuotationSyncRepositoryError) as exc:
        await jobs.session.rollback()
        status = await jobs.fail(
            job.id,
            str(exc),
            retry_backoff_seconds=quotation_sync_backoff(
                job.attempts,
                settings.quotation_sync_backoff_seconds,
            ),
        )
        return {
            "job_id": str(job.id),
            "offer_number": job.offer_number,
            "status": status,
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        logger.exception("unexpected quotation sync failure job=%s", job.id)
        await jobs.session.rollback()
        status = await jobs.fail(
            job.id,
            str(exc),
            retry_backoff_seconds=quotation_sync_backoff(
                job.attempts,
                settings.quotation_sync_backoff_seconds,
            ),
        )
        return {
            "job_id": str(job.id),
            "offer_number": job.offer_number,
            "status": status,
            "error": str(exc),
        }
