"""GAEB validator tests: penny reconciliation and optional XSD handling."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.parsers.gaeb_validator import GaebValidationError, validate_gaeb, validate_gaeb_file

pytest.importorskip("pygaeb")

from pygaeb import ExchangePhase, GAEBParser, GAEBWriter  # noqa: E402
from pygaeb.builder import BoQBuilder  # noqa: E402


class FakeItem:
    def __init__(
        self,
        *,
        oz: str = "1.1",
        qty: Decimal | None = Decimal("10"),
        unit_price: Decimal | None = Decimal("2.00"),
        total_price: Decimal | None = Decimal("20.00"),
        affects_total: bool = True,
    ) -> None:
        self.full_oz = oz
        self.qty = qty
        self.unit_price = unit_price
        self.total_price = total_price
        self.affects_total = affects_total


class FakeDocument:
    def __init__(
        self,
        items: list[FakeItem],
        *,
        grand_total: Decimal | None = Decimal("20.00"),
        computed_grand_total: Decimal | None = Decimal("20.00"),
    ) -> None:
        self._items = items
        self.grand_total = grand_total
        self.computed_grand_total = computed_grand_total
        self.award = None
        self.exchange_phase = None

    def iter_items(self) -> list[FakeItem]:
        return self._items


def build_x83(workspace_tmp: Path) -> Path:
    path = workspace_tmp / "tender.X83"
    builder = BoQBuilder(phase="X83")
    builder.project(project_name="Rahmenvertrag", client="Stadt Muster", award_no="T-1", currency="EUR")
    lot = builder.add_lot("1", "Los 1")
    category = lot.add_category("1", "Reinigung")
    category.add_item(oz="1.1", short_text="Nitrilhandschuhe", qty=Decimal("10"), unit="Stk")
    document = builder.build()
    GAEBWriter.write(document, path, phase=ExchangePhase.X83)
    return path


def test_valid_document_without_xsd_is_valid_with_warning() -> None:
    report = validate_gaeb(FakeDocument([FakeItem()]))

    assert report.valid is True
    assert report.xsd_checked is False
    assert any(issue.code == "XSD_NOT_CONFIGURED" for issue in report.warnings)


def test_required_xsd_turns_missing_schema_into_error() -> None:
    report = validate_gaeb(FakeDocument([FakeItem()]), require_xsd=True)

    assert report.valid is False
    assert any(issue.code == "XSD_NOT_CONFIGURED" for issue in report.errors)


def test_item_math_mismatch_is_detected() -> None:
    report = validate_gaeb(FakeDocument([FakeItem(total_price=Decimal("19.99"))]))

    assert report.valid is False
    assert any(issue.code == "ITEM_TOTAL_MISMATCH" for issue in report.errors)


def test_grand_total_mismatch_is_detected() -> None:
    report = validate_gaeb(FakeDocument([FakeItem()], grand_total=Decimal("99.00")))

    assert report.valid is False
    assert any(issue.code == "GRAND_TOTAL_MISMATCH" for issue in report.errors)


def test_duplicate_position_is_detected() -> None:
    report = validate_gaeb(FakeDocument([FakeItem(), FakeItem()]))

    assert report.valid is False
    assert any(issue.code == "DUPLICATE_POSITION" for issue in report.errors)


def test_raise_if_invalid() -> None:
    report = validate_gaeb(FakeDocument([FakeItem(total_price=Decimal("1.00"))]))

    with pytest.raises(GaebValidationError):
        report.raise_if_invalid()


def test_real_x84_validates(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    path = build_x83(workspace_tmp)
    document = GAEBParser.parse(path)
    for item in document.iter_items():
        item.unit_price = Decimal("11.00")
        item.total_price = Decimal("110.00")
    bid_path = workspace_tmp / "bid.x84"
    GAEBWriter.write(document, bid_path, phase=ExchangePhase.X84)

    report = validate_gaeb_file(bid_path)

    assert report.valid is True
    assert report.xsd_checked is False


def test_missing_file_returns_parse_error(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    report = validate_gaeb_file(workspace_tmp / "missing.x83")

    assert report.valid is False
    assert report.errors[0].code == "GAEB_PARSE"
