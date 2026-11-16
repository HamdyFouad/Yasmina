"""Deterministic seed for the Playwright golden-path suite.

Creates (per invocation, under a unique suffix):

* an ``approver`` user for the four-eyes approval,
* an ``admin`` user for the invoice/payment and outbox screens,
* a mock published ``Tender`` owned by ``e2e-sales``,
* a ``PENDING_APPROVAL`` customer offer owned by ``e2e-sales`` (so the approver
  is a different person), and
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
from src.models.orm import QuotationSyncJob
from src.models.schemas import CustomerOffer, CustomerOfferLine, Tender, q2, q4
from src.repositories.offer_repository import OfferRepository
from src.repositories.tender_repository import TenderRepository
from src.repositories.user_repository import UserRepository

__all__ = ["main", "seed"]

APPROVER_PASSWORD = "E2E-Approver-2026!"
ADMIN_PASSWORD = "E2E-Admin-2026!"
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


def _offer(*, offer_number: str, tender_id: UUID, status: CustomerOfferStatus) -> CustomerOffer:
    line = _line()
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    return CustomerOffer(
        offer_number=offer_number,
        customer_name=CUSTOMER_NAME,
        customer_reference="E2E-REF-1",
        tender_id=tender_id,
        status=status,
        lines=[line],
        margin_rate=Decimal("0.15"),
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date.today() + timedelta(days=30),
        owner="e2e-sales",
    )


async def seed(settings: Settings, *, suffix: str) -> dict[str, str]:
    approver_username = f"e2e-approver-{suffix}"
    admin_username = f"e2e-admin-{suffix}"
    offer_number = f"E2E-{suffix}"
    outbox_offer_number = f"E2E-OBX-{suffix}"
    tender_number = f"E2E-TND-{suffix}"

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

            tender = Tender(
                tender_number=tender_number[:64],
                title=f"E2E Rahmenvertrag Hygiene {suffix}",
                client_name=CUSTOMER_NAME,
                procedure=ProcurementProcedure.OPEN_PROCEDURE,
                source_format=TenderSourceFormat.PORTAL,
                submission_deadline=datetime.now(UTC) + timedelta(days=30),
                status=TenderStatus.PUBLISHED,
                owner="e2e-sales",
            )
            await TenderRepository(session).save(tender)

            offer = (
                await OfferRepository(session).save(
                    _offer(
                        offer_number=offer_number,
                        tender_id=tender.id,
                        status=CustomerOfferStatus.PENDING_APPROVAL,
                    )
                )
            ).offer
            outbox_offer = (
                await OfferRepository(session).save(
                    _offer(
                        offer_number=outbox_offer_number,
                        tender_id=tender.id,
                        status=CustomerOfferStatus.DRAFT,
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
            await session.commit()
    finally:
        await dispose_engine()

    return {
        "approver": {"username": approver_username, "password": APPROVER_PASSWORD},
        "admin": {"username": admin_username, "password": ADMIN_PASSWORD},
        "offer_number": offer_number,
        "outbox_offer_number": outbox_offer_number,
        "tender_number": tender_number,
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
