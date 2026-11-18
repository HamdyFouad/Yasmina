"""Deterministic seed for the Playwright golden-path suite.

Creates (per invocation, under a unique suffix):

* an ``approver`` user for the four-eyes approval,
* an ``admin`` user for the invoice/payment and outbox screens,
* a ``sales`` user for the tender-to-export workflow,
* a mock published ``Tender`` owned by that sales user,
* a ``PENDING_APPROVAL`` customer offer owned by that sales user (so the
  approver is a different person), and
* a second offer with a terminal ``FAILED`` quotation-sync job for the retry
  flow.

The script only writes PostgreSQL rows; it never calls the API, MinIO or the
outbox worker. It prints the fixture file consumed by Playwright.

Example::

    python scripts/seed_e2e.py --suffix ci-123-1 \
        --output frontend/tests/e2e/.fixtures.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.api.security import Role
from src.config import Settings, get_settings
from src.db import dispose_engine, get_session_factory
from src.models.enums import (
    CustomerOfferStatus,
    ProcurementProcedure,
    TenderSourceFormat,
    TenderStatus,
    UnitOfMeasure,
)
from src.models.orm import (
    Customer,
    QuotationSyncJob,
    Supplier,
    SupplierContact,
    TenderIngestionJob,
    WorkerCommand,
    WorkerRun,
)
from src.models.schemas import CustomerOffer, CustomerOfferLine, Tender, q2, q4
from src.repositories.offer_repository import OfferRepository
from src.repositories.tender_repository import TenderRepository
from src.repositories.user_repository import UserRepository

__all__ = ["main", "seed"]

APPROVER_PASSWORD = "E2E-Approver-2026!"
ADMIN_PASSWORD = "E2E-Admin-2026!"
SALES_PASSWORD = "E2E-Sales-2026!"
BUYER_REFERENCE = "991-03730-19"
CUSTOMER_NAME = "E2E Stadt Muster"


def _safe_suffix(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return cleaned[:40] or datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _line() -> CustomerOfferLine:
    cost = Decimal("10.0000")
    margin = Decimal("0.15")
    return CustomerOfferLine(
        position_number="1.1",
        description="Nitrilhandschuhe blau Gr. M (E2E)",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
        cost_unit_price_net=cost,
        margin_rate=margin,
        unit_price_net=q4(cost * (Decimal(1) + margin)),
        total_net=q2(Decimal("10.0000") * q4(cost * (Decimal(1) + margin))),
    )


def _freight_line() -> CustomerOfferLine:
    amount = Decimal("25.00")
    return CustomerOfferLine(
        position_number="FREIGHT",
        description="Freight (flat)",
        quantity=Decimal("1.0000"),
        unit=UnitOfMeasure.STUECK,
        cost_unit_price_net=amount,
        margin_rate=Decimal("0.0000"),
        unit_price_net=amount,
        total_net=amount,
    )


def _pallet_line() -> CustomerOfferLine:
    count = Decimal("2.0000")
    unit_price = Decimal("12.5000")
    return CustomerOfferLine(
        position_number="PALLET",
        description="Pallet fee (2 pallet(s))",
        quantity=count,
        unit=UnitOfMeasure.PALETTE,
        cost_unit_price_net=unit_price,
        margin_rate=Decimal("0.0000"),
        unit_price_net=unit_price,
        total_net=q2(count * unit_price),
    )


def _offer(
    *,
    offer_number: str,
    tender_id: UUID,
    status: CustomerOfferStatus,
    owner: str,
) -> CustomerOffer:
    line = _line()
    lines = [line, _freight_line(), _pallet_line()]
    net = q2(sum((offer_line.total_net for offer_line in lines), start=Decimal("0.00")))
    vat = q2(net * Decimal("0.19"))
    return CustomerOffer(
        offer_number=offer_number,
        customer_name=CUSTOMER_NAME,
        customer_reference="E2E-REF-1",
        tender_id=tender_id,
        status=status,
        lines=lines,
        margin_rate=Decimal("0.15"),
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date.today() + timedelta(days=30),
        owner=owner,
    )


async def seed(settings: Settings, *, suffix: str) -> dict[str, str]:
    approver_username = f"e2e-approver-{suffix}"
    admin_username = f"e2e-admin-{suffix}"
    sales_username = f"e2e-sales-{suffix}"
    offer_number = f"E2E-{suffix}"
    outbox_offer_number = f"E2E-OBX-{suffix}"
    tender_number = f"E2E-TND-{suffix}"
    quarantine_tender_number = f"E2E-QTN-{suffix}"
    supplier_name = f"E2E Hygiene Supplier {suffix}"
    master_customer_name = f"E2E Customer {suffix}"

    factory = get_session_factory(settings)
    try:
        async with factory() as session:
            users = UserRepository(session, password_min_length=settings.password_min_length)
            await users.create(
                approver_username,
                APPROVER_PASSWORD,
                [Role.APPROVER],
                email="e2e-approver@example.test",
            )
            await users.create(
                admin_username,
                ADMIN_PASSWORD,
                [Role.ADMIN],
                email="e2e-admin@example.test",
            )
            await users.create(
                sales_username,
                SALES_PASSWORD,
                [Role.SALES],
                email="e2e-sales@example.test",
            )

            tender = Tender(
                tender_number=tender_number[:64],
                title=f"E2E Rahmenvertrag Hygiene {suffix}",
                client_name=CUSTOMER_NAME,
                procedure=ProcurementProcedure.OPEN_PROCEDURE,
                source_format=TenderSourceFormat.PORTAL,
                submission_deadline=datetime.now(UTC) + timedelta(days=30),
                status=TenderStatus.PUBLISHED,
                owner=sales_username,
            )
            await TenderRepository(session).save(tender)

            offer = (
                await OfferRepository(session).save(
                    _offer(
                        offer_number=offer_number,
                        tender_id=tender.id,
                        status=CustomerOfferStatus.PENDING_APPROVAL,
                        owner=sales_username,
                    )
                )
            ).offer
            outbox_offer = (
                await OfferRepository(session).save(
                    _offer(
                        offer_number=outbox_offer_number,
                        tender_id=tender.id,
                        status=CustomerOfferStatus.DRAFT,
                        owner=sales_username,
                    )
                )
            ).offer

            session.add(
                QuotationSyncJob(
                    offer_id=outbox_offer.id,
                    offer_number=outbox_offer.offer_number,
                    status="failed",
                    attempts=1,
                    max_attempts=1,
                    available_at=datetime.now(UTC),
                    last_error="E2E seeded ERPNext failure",
                )
            )

            quarantine_tender = Tender(
                tender_number=quarantine_tender_number[:64],
                title=f"E2E Quarantined Hygiene {suffix}",
                client_name=CUSTOMER_NAME,
                procedure=ProcurementProcedure.OPEN_PROCEDURE,
                source_format=TenderSourceFormat.PORTAL,
                submission_deadline=datetime.now(UTC) + timedelta(days=30),
                status=TenderStatus.PUBLISHED,
                owner=sales_username,
            )
            await TenderRepository(session).save(quarantine_tender)
            session.add_all(
                [
                    TenderIngestionJob(
                        tender_id=tender.id,
                        portal_name="bayern",
                        status="failed",
                        attempts=5,
                        max_attempts=5,
                        available_at=datetime.now(UTC),
                        last_error="E2E seeded dead-letter: parser failed",
                    ),
                    TenderIngestionJob(
                        tender_id=quarantine_tender.id,
                        portal_name="bund",
                        status="failed",
                        attempts=5,
                        max_attempts=5,
                        available_at=datetime.now(UTC),
                        last_error="SECURITY: Eicar-Test-Signature",
                        quarantined=True,
                        quarantine_reason="Eicar-Test-Signature",
                    ),
                    WorkerRun(
                        run_type="scrape",
                        resource="bayern",
                        status="succeeded",
                        worker_id="e2e-worker",
                        started_at=datetime.now(UTC) - timedelta(minutes=5),
                        finished_at=datetime.now(UTC) - timedelta(minutes=4),
                        duration_ms=60_000,
                        items_processed=3,
                        errors_count=0,
                        details={"pages_fetched": 1, "source": "e2e-seed"},
                    ),
                    WorkerRun(
                        run_type="ingestion",
                        resource="outbox",
                        status="failed",
                        worker_id="e2e-worker",
                        started_at=datetime.now(UTC) - timedelta(minutes=10),
                        finished_at=datetime.now(UTC) - timedelta(minutes=9),
                        duration_ms=60_000,
                        items_processed=1,
                        errors_count=1,
                        error="E2E seeded ingestion failure",
                        details={"processed": 1, "failed": 1, "source": "e2e-seed"},
                    ),
                    Supplier(
                        name=supplier_name,
                        legal_name=f"{supplier_name} GmbH",
                        email="rfq@hygiene-gross.de",
                        phone="+49 941 123456",
                        street="Gewerbepark 7",
                        postal_code="93055",
                        city="Regensburg",
                        country_code="DE",
                        payment_terms="Payable within 30 days",
                        delivery_days=5,
                        contacts=[
                            SupplierContact(
                                name="Erika Muster",
                                role="Key Account",
                                email="rfq@hygiene-gross.de",
                                phone="+49 941 123456",
                                is_primary=True,
                            )
                        ],
                    ),
                    Customer(
                        name=master_customer_name,
                        legal_name=f"{master_customer_name} Stadt",
                        street="Rathausplatz 1",
                        postal_code="93047",
                        city="Regensburg",
                        country_code="DE",
                        vat_id="DE123456789",
                        leitweg_id=BUYER_REFERENCE,
                        payment_terms="Payable within 14 days",
                        contact_name="Einkauf Stadt",
                        contact_email="einkauf@musterstadt.de",
                    ),
                    WorkerCommand(
                        command_type="scrape",
                        status="queued",
                        portal="all",
                        max_pages=1,
                        requested_by=admin_username,
                        details={"source": "e2e-seed"},
                    ),
                    WorkerCommand(
                        command_type="ingestion",
                        status="failed",
                        batch_limit=10,
                        requested_by=admin_username,
                        error="E2E seeded command failure",
                        details={"source": "e2e-seed"},
                    ),
                    *[
                        WorkerRun(
                            run_type="scrape",
                            resource=f"e2e-extra-{index:02d}",
                            status="succeeded",
                            worker_id="e2e-worker",
                            started_at=datetime.now(UTC) - timedelta(hours=index + 1),
                            finished_at=datetime.now(UTC)
                            - timedelta(hours=index + 1)
                            + timedelta(seconds=30),
                            duration_ms=30_000,
                            items_processed=1,
                            errors_count=0,
                            details={"source": "e2e-seed", "page_fixture": index},
                        )
                        for index in range(21)
                    ],
                ]
            )
            await session.commit()
    finally:
        await dispose_engine()

    return {
        "approver": {"username": approver_username, "password": APPROVER_PASSWORD},
        "admin": {"username": admin_username, "password": ADMIN_PASSWORD},
        "sales": {"username": sales_username, "password": SALES_PASSWORD},
        "offer_number": offer_number,
        "outbox_offer_number": outbox_offer_number,
        "tender_number": tender_number,
        "quarantine_tender_number": quarantine_tender_number,
        "supplier_name": supplier_name,
        "master_customer_name": master_customer_name,
        "customer_name": CUSTOMER_NAME,
        "buyer_reference": BUYER_REFERENCE,
        "invoice_net": str(offer.total_net),
        "invoice_gross": str(offer.total_gross),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed-e2e",
        description="Provision deterministic users, tender, offers and a failed outbox job.",
    )
    parser.add_argument(
        "--suffix",
        default=datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
        help="unique suffix for usernames/offer numbers (default: current UTC timestamp)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/tests/e2e/.fixtures.json"),
        help="fixture file consumed by Playwright",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    suffix = _safe_suffix(args.suffix)
    try:
        fixtures = asyncio.run(seed(get_settings(), suffix=suffix))
    except Exception as exc:  # pragma: no cover - operator-facing CLI boundary
        print(f"seed-e2e failed: {exc}", file=sys.stderr)
        return 1

    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixtures, indent=2) + "\n", encoding="utf-8")
    print(
        f"seeded E2E fixtures for {fixtures['offer_number']} "
        f"(approver={fixtures['approver']['username']}) -> {output}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
