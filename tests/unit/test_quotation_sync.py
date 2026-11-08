"""Quotation outbox worker tests: success, backoff and missing credentials."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from src.config import Settings
from src.integrations.erpnext_client import ERPNextError
from src.models.enums import CustomerOfferStatus, UnitOfMeasure
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.repositories.quotation_sync_repository import QuotationSyncJobView
from src.services.quotation_sync import (
    _process_one,
    process_quotation_sync_jobs_once,
    quotation_sync_backoff,
)


def make_offer() -> CustomerOffer:
    line = CustomerOfferLine(
        position_number="1.1",
        description="Nitrilhandschuhe",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
        cost_unit_price_net=Decimal("10.0000"),
        margin_rate=Decimal("0.15"),
        unit_price_net=q4(Decimal("10.0000") * Decimal("1.15")),
        total_net=q2(Decimal("10.0000") * q4(Decimal("10.0000") * Decimal("1.15"))),
    )
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    return CustomerOffer(
        offer_number="AN-2026-0001",
        customer_name="Stadt Muster",
        status=CustomerOfferStatus.APPROVED,
        lines=[line],
        margin_rate=Decimal("0.15"),
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
        ed25519_approval_signature="a" * 128,
        approval_payload_hash="a" * 64,
        approved_by="C. Mueller",
        approved_at=datetime.now(UTC),
    )


def make_job(*, attempts: int = 1) -> QuotationSyncJobView:
    return QuotationSyncJobView(
        id=uuid4(),
        offer_id=uuid4(),
        offer_number="AN-2026-0001",
        status="processing",
        attempts=attempts,
        max_attempts=6,
        available_at=datetime.now(UTC),
        quotation_name=None,
        last_error=None,
    )


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        erpnext_base_url="https://erp.example.test",
        erpnext_api_key="key",
        erpnext_api_secret="secret",
        quotation_sync_backoff_seconds=10,
    )


class FakeJobs:
    def __init__(self) -> None:
        self.session = SimpleNamespace(rollback=self._rollback)
        self.rollbacks = 0
        self.completed: list[tuple[UUID, str]] = []
        self.skipped: list[tuple[UUID, str | None]] = []
        self.failed: list[tuple[UUID, str, int]] = []

    async def _rollback(self) -> None:
        self.rollbacks += 1

    async def complete(self, job_id: UUID, *, quotation_name: str) -> None:
        self.completed.append((job_id, quotation_name))

    async def skip(self, job_id: UUID, reason: str | None = None) -> None:
        self.skipped.append((job_id, reason))

    async def fail(self, job_id: UUID, error: str, *, retry_backoff_seconds: int) -> str:
        self.failed.append((job_id, error, retry_backoff_seconds))
        return "pending"


class FakeOffers:
    def __init__(self, offer: CustomerOffer | None) -> None:
        self.offer = offer
        self.saved: list[CustomerOffer] = []

    async def get(self, offer_id: UUID) -> Any:
        if self.offer is None or offer_id != self.offer.id:
            return None
        return SimpleNamespace(offer=self.offer)

    async def save(self, offer: CustomerOffer, *, commit: bool = True) -> Any:
        self.saved.append(offer)
        self.offer = offer
        return SimpleNamespace(offer=offer)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class FakeERP:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def create_quotation(self, offer: CustomerOffer) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(name="QTN-2026-0001", created=True)


def test_backoff_is_exponential_and_capped() -> None:
    assert quotation_sync_backoff(1, 60) == 60
    assert quotation_sync_backoff(2, 60) == 120
    assert quotation_sync_backoff(3, 60) == 240
    assert quotation_sync_backoff(20, 60) == 3600


async def test_successful_sync_updates_offer_and_audits() -> None:
    offer = make_offer()
    job = replace(make_job(), offer_id=offer.id)
    jobs = FakeJobs()
    offers = FakeOffers(offer)
    audit = FakeAudit()
    client = FakeERP()

    summary = await _process_one(
        job,
        jobs=jobs,  # type: ignore[arg-type]
        offers=offers,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        settings=make_settings(),
    )

    assert summary["status"] == "succeeded"
    assert summary["quotation_name"] == "QTN-2026-0001"
    assert client.calls == 1
    assert jobs.completed == [(job.id, "QTN-2026-0001")]
    assert offers.saved[0].erpnext_quotation_name == "QTN-2026-0001"
    assert audit.events[0]["event_type"].value == "ERP_QUOTATION_CREATED"
    assert audit.events[0]["payload"]["job_id"] == str(job.id)


async def test_transient_failure_records_exponential_backoff() -> None:
    offer = make_offer()
    job = replace(make_job(attempts=2), offer_id=offer.id)
    jobs = FakeJobs()
    offers = FakeOffers(offer)
    client = FakeERP(error=ERPNextError("ERPNext unavailable", status_code=503))

    summary = await _process_one(
        job,
        jobs=jobs,  # type: ignore[arg-type]
        offers=offers,  # type: ignore[arg-type]
        audit=FakeAudit(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        settings=make_settings(),
    )

    assert summary["status"] == "pending"
    assert jobs.rollbacks == 1
    assert jobs.failed[0][2] == 20
    assert "ERPNext unavailable" in jobs.failed[0][1]
    assert offers.saved == []


async def test_missing_offer_is_skipped() -> None:
    job = make_job()
    jobs = FakeJobs()

    summary = await _process_one(
        job,
        jobs=jobs,  # type: ignore[arg-type]
        offers=FakeOffers(None),  # type: ignore[arg-type]
        audit=FakeAudit(),  # type: ignore[arg-type]
        client=FakeERP(),  # type: ignore[arg-type]
        settings=make_settings(),
    )

    assert summary["status"] == "skipped"
    assert jobs.skipped[0][0] == job.id


async def test_unconfigured_erp_returns_without_touching_the_queue() -> None:
    summaries = await process_quotation_sync_jobs_once(
        settings=Settings(_env_file=None, database_url=None, erpnext_api_key=None),
    )
    assert summaries == []
