"""PDF LV parser tests using injected Docling fakes (no Docling required)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.parsers import pdf_parser
from src.parsers.pdf_parser import PdfParseError, parse_pdf


class FakeFrame:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    def to_dict(self, orient: str) -> list[dict[str, str]]:
        assert orient == "records"
        return self.rows


class FakeTable:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    def export_to_dataframe(self, doc: object | None = None) -> FakeFrame:
        return FakeFrame(self.rows)


class FakeGridTable:
    class Cell:
        def __init__(self, text: str) -> None:
            self.text = text

    class Data:
        def __init__(self, grid: list[list["FakeGridTable.Cell"]]) -> None:
            self.grid = grid

    def __init__(self, rows: list[list[str]]) -> None:
        self.data = self.Data([[self.Cell(cell) for cell in row] for row in rows])


class FakeDocument:
    def __init__(self, tables: list[object]) -> None:
        self.tables = tables


class FakeResult:
    def __init__(self, tables: list[object]) -> None:
        self.document = FakeDocument(tables)
        self.has_errors = False


class FakeConverter:
    def __init__(self, tables: list[object]) -> None:
        self.tables = tables

    def convert(self, path: Path) -> FakeResult:
        return FakeResult(self.tables)


def make_pdf(workspace_tmp: Path) -> Path:
    path = workspace_tmp / "lv.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def test_parse_german_lv_table(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    pdf = make_pdf(workspace_tmp)
    table = FakeTable(
        [
            {
                "Pos.": "1.1",
                "Menge": "10",
                "Einheit": "Karton",
                "Kurztext": "Nitrilhandschuhe blau Gr. M",
                "Langtext": "1000 Stück je Karton",
            },
            {
                "Pos.": "1.2",
                "Menge": "2,5",
                "Einheit": "Pack",
                "Kurztext": "Papierhandtücher weiß 2-lagig",
                "Langtext": "",
            },
        ]
    )

    result = parse_pdf(pdf, converter=FakeConverter([table]))

    assert result.table_count == 1
    assert result.converter_name == "FakeConverter"
    first, second = result.items

    assert first.position_number == "1.1"
    assert first.quantity == 10
    assert first.unit is UnitOfMeasure.KARTON
    assert first.material is MaterialType.NITRILE
    assert first.color is StandardizedColor.BLUE
    assert first.glove_size is GloveSize.M
    assert first.pack_size == 1000
    assert second.quantity == Decimal("2.5")
    assert second.unit is UnitOfMeasure.PACK


def test_parse_grid_table_and_skip_bad_rows(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    pdf = make_pdf(workspace_tmp)
    grid = FakeGridTable(
        [
            ["Pos.", "Menge", "Einheit", "Kurztext"],
            ["2.1", "3", "Stk.", "Müllbeutel schwarz 120 l"],
            ["2.2", "", "Stk.", "Ohne Menge"],
            ["2.1", "5", "Stk.", "Doppelte Position"],
        ]
    )

    result = parse_pdf(pdf, converter=FakeConverter([grid]))

    assert [item.position_number for item in result.items] == ["2.1"]
    assert result.items[0].unit is UnitOfMeasure.STUECK
    assert any("no positive quantity" in warning for warning in result.warnings)
    assert any("duplicate PDF position" in warning for warning in result.warnings)


def test_no_tables_is_an_error(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    pdf = make_pdf(workspace_tmp)

    with pytest.raises(PdfParseError, match="no tables"):
        parse_pdf(pdf, converter=FakeConverter([]))


def test_no_items_is_an_error(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    pdf = make_pdf(workspace_tmp)
    table = FakeTable([{"Pos.": "", "Menge": "1", "Einheit": "Stk.", "Kurztext": ""}])

    with pytest.raises(PdfParseError, match="no LV positions"):
        parse_pdf(pdf, converter=FakeConverter([table]))


def test_production_path_invokes_the_default_docling_converter(
    workspace_tmp, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    pdf = make_pdf(workspace_tmp)
    table = FakeTable(
        [{"Pos.": "1.1", "Menge": "1", "Einheit": "Stk.", "Kurztext": "Handschuhe"}]
    )
    converter = FakeConverter([table])
    calls: list[Path] = []

    def fake_default() -> FakeConverter:
        calls.append(pdf)
        return converter

    monkeypatch.setattr(pdf_parser, "_default_converter", fake_default)

    result = parse_pdf(pdf)

    assert calls == [pdf]
    assert result.converter_name == "FakeConverter"
    assert result.items[0].unit is UnitOfMeasure.STUECK
