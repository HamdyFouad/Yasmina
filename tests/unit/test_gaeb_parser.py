"""GAEB parser/writer tests using real pyGAEB-generated X83/X84 files."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.models.enums import (
    CustomerOfferStatus,
    GloveSize,
    MaterialType,
    StandardizedColor,
    TenderSourceFormat,
    UnitOfMeasure,
)
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.parsers.gaeb_parser import GaebExportError, parse_gaeb, write_x84
from src.services.approval_service import ApprovalService, generate_keypair, sign_offer

pygaeb = pytest.importorskip("pygaeb")

from pygaeb import ExchangePhase, GAEBParser, GAEBWriter  # noqa: E402
from pygaeb.builder import BoQBuilder  # noqa: E402

PRIVATE_KEY_HEX, PUBLIC_KEY_HEX = generate_keypair()
APPROVAL = ApprovalService(PUBLIC_KEY_HEX)


def build_x83(workspace_tmp: Path) -> Path:
    path = workspace_tmp / "tender.X83"
    builder = BoQBuilder(phase="X83")
    builder.project(
        project_name="Rahmenvertrag Hygiene",
        client="Stadt Muster",
        award_no="T-2026-001",
        currency="EUR",
        description="Reinigungsmittel",
    )
    lot = builder.add_lot("1", "Los 1")
    category = lot.add_category("1", "Reinigung")
    handle = category.add_item(
        oz="1.1",
        short_text="Nitrilhandschuhe blau Gr. M",
        qty=Decimal("10"),
        unit="Stk",
    )
    handle.set_long_text("1000 Stück je Karton")
    document = builder.build()
    GAEBWriter.write(document, path, phase=ExchangePhase.X83)
    return path


def make_offer(
    item_position: str, *, status: CustomerOfferStatus = CustomerOfferStatus.APPROVED
) -> CustomerOffer:
    cost = Decimal("10.0000")
    margin = Decimal("0.10")
    unit_price = q4(cost * (Decimal(1) + margin))
    line = CustomerOfferLine(
        position_number=item_position,
        description="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.STUECK,
        cost_unit_price_net=cost,
        margin_rate=margin,
        unit_price_net=unit_price,
        total_net=q2(Decimal("10.0000") * unit_price),
    )
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    draft = CustomerOffer(
        offer_number="AN-2026-0001",
        customer_name="Stadt Muster",
        status=CustomerOfferStatus.DRAFT,
        lines=[line],
        margin_rate=margin,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
    )
    if status is CustomerOfferStatus.APPROVED:
        return sign_offer(
            draft,
            PRIVATE_KEY_HEX,
            approver="C. Mueller",
            approved_at=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        )
    return draft


def test_parse_x83_into_tender(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    path = build_x83(workspace_tmp)

    result = parse_gaeb(path)

    tender = result.tender
    assert tender.tender_number == "T-2026-001"
    assert tender.title == "Rahmenvertrag Hygiene"
    assert tender.client_name == "Stadt Muster"
    assert tender.source_format is TenderSourceFormat.GAEB_X83
    assert tender.currency.value == "EUR"
    assert tender.submission_deadline.tzinfo is not None
    assert len(tender.items) == 1

    item = tender.items[0]
    assert item.material is MaterialType.NITRILE
    assert item.color is StandardizedColor.BLUE
    assert item.glove_size is GloveSize.M
    assert item.unit is UnitOfMeasure.STUECK
    assert item.pack_size == 1000
    assert item.quantity == Decimal("10")
    assert result.exchange_phase


def test_write_x84_with_approved_offer(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    path = build_x83(workspace_tmp)
    parsed = parse_gaeb(path)
    offer = make_offer(parsed.tender.items[0].position_number)
    output = workspace_tmp / "bid.x84"

    warnings = write_x84(parsed.document, offer, output, approval_service=APPROVAL)

    assert output.is_file()
    assert isinstance(warnings, tuple)
    written = GAEBParser.parse(output)
    written_item = next(written.iter_items())
    assert written_item.unit_price == Decimal("11.0000")
    assert written_item.total_price == Decimal("110.00")
    assert written.grand_total == Decimal("110.00")


def test_write_x84_rejects_wrong_approval_key(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    path = build_x83(workspace_tmp)
    parsed = parse_gaeb(path)
    offer = make_offer(parsed.tender.items[0].position_number)
    _, wrong_public = generate_keypair()

    with pytest.raises(GaebExportError, match="does not verify"):
        write_x84(
            parsed.document,
            offer,
            workspace_tmp / "bid.x84",
            approval_service=ApprovalService(wrong_public),
        )


def test_write_x84_requires_approval(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    path = build_x83(workspace_tmp)
    parsed = parse_gaeb(path)
    offer = make_offer(
        parsed.tender.items[0].position_number,
        status=CustomerOfferStatus.DRAFT,
    )

    with pytest.raises(GaebExportError, match="not approved"):
        write_x84(parsed.document, offer, workspace_tmp / "bid.x84", approval_service=APPROVAL)


def test_write_x84_requires_x84_suffix(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    path = build_x83(workspace_tmp)
    parsed = parse_gaeb(path)
    offer = make_offer(parsed.tender.items[0].position_number)

    with pytest.raises(GaebExportError, match=".x84"):
        write_x84(parsed.document, offer, workspace_tmp / "bid.x83", approval_service=APPROVAL)


def test_parse_rejects_unsupported_extension(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    bogus = workspace_tmp / "tender.pdf"
    bogus.write_text("not gaeb", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported"):
        parse_gaeb(bogus)
