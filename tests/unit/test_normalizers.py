"""Unit tests for the pre-validation normalization layer (Section 4.0)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from src.models.enums import (
    GloveSize,
    MaterialType,
    StandardizedColor,
    UnitOfMeasure,
)
from src.parsers.normalizers import (
    NormalizationError,
    normalize_color,
    normalize_decimal,
    normalize_glove_size,
    normalize_material,
    normalize_pack_size,
    normalize_percentage,
    normalize_quantity,
    normalize_raw_extraction,
    normalize_unit_of_measure,
)


class StrictLineItem(BaseModel):
    """Minimal strict schema mirroring the domain models."""

    model_config = ConfigDict(strict=True, extra="forbid")

    glove_size: GloveSize
    color: StandardizedColor
    material: MaterialType
    unit: UnitOfMeasure


# ---------------------------------------------------------------------------
# Glove sizes (EN 420)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, GloveSize.NOT_APPLICABLE),
        ("Gr. 8", GloveSize.M),
        ("gr.8", GloveSize.M),
        ("Größe 8", GloveSize.M),
        ("Handschuh Größe 7,5", GloveSize.M),
        ("8,5", GloveSize.L),
        ("9.0", GloveSize.L),
        ("10.5", GloveSize.XXL),
        ("XXL", GloveSize.XXL),
        ("klein", GloveSize.S),
        ("mittel", GloveSize.M),
        ("groß", GloveSize.L),
        ("gross", GloveSize.L),
        (8, GloveSize.M),
        ("nicht angegeben", GloveSize.NOT_APPLICABLE),
    ],
)
def test_normalize_glove_size(raw: object, expected: GloveSize) -> None:
    assert normalize_glove_size(raw) is expected


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("blau", StandardizedColor.BLUE),
        ("Blau", StandardizedColor.BLUE),
        ("schwarz", StandardizedColor.BLACK),
        ("gelb", StandardizedColor.YELLOW),
        ("rot", StandardizedColor.RED),
        ("grün", StandardizedColor.GREEN),
        ("gruen", StandardizedColor.GREEN),
        ("Grau", StandardizedColor.GREY),
        ("anthrazit", StandardizedColor.GREY),
        ("braun", StandardizedColor.BROWN),
        ("orange", StandardizedColor.ORANGE),
        ("rosa", StandardizedColor.PINK),
        ("pink", StandardizedColor.PINK),
        ("violett", StandardizedColor.PURPLE),
        ("lila", StandardizedColor.PURPLE),
        ("weiß", StandardizedColor.WHITE),
        ("weiss", StandardizedColor.WHITE),
        ("hochweiß", StandardizedColor.WHITE),
        ("transparent", StandardizedColor.TRANSPARENT),
        ("transluzent", StandardizedColor.TRANSPARENT),
        ("klar", StandardizedColor.CLEAR),
        ("glasklar", StandardizedColor.CLEAR),
        ("natur", StandardizedColor.NATURAL),
        ("bedruckt", StandardizedColor.PRINTED_MULTICOLOR),
        ("mehrfarbig", StandardizedColor.PRINTED_MULTICOLOR),
        ("transparent/klar", StandardizedColor.TRANSPARENT),
        ("blau/weiß", StandardizedColor.BLUE),
        ("blau (ähnlich RAL 5010)", StandardizedColor.BLUE),
        ("", StandardizedColor.UNKNOWN),
        (None, StandardizedColor.UNKNOWN),
        ("regenbogen", StandardizedColor.UNKNOWN),
    ],
)
def test_normalize_color(raw: object, expected: StandardizedColor) -> None:
    assert normalize_color(raw) is expected


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Nitril", MaterialType.NITRILE),
        ("nitrilkautschuk", MaterialType.NITRILE),
        ("NBR", MaterialType.NITRILE),
        ("Naturlatex", MaterialType.LATEX),
        ("LDPE", MaterialType.POLYETHYLENE),
        ("HDPE-Folie", MaterialType.POLYETHYLENE),
        ("Recyclingpapier", MaterialType.RECYCLED_PAPER),
        ("PVC", MaterialType.PVC),
        ("Baumwolle", MaterialType.COTTON),
        ("Mikrofaser", MaterialType.MICROFIBER),
        (None, MaterialType.NOT_APPLICABLE),
        ("Edelstahl", MaterialType.NOT_APPLICABLE),
    ],
)
def test_normalize_material(raw: object, expected: MaterialType) -> None:
    assert normalize_material(raw) is expected


# ---------------------------------------------------------------------------
# Units of measure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Karton à 1000 Stk", UnitOfMeasure.KARTON),
        ("Stk.", UnitOfMeasure.STUECK),
        ("Stück", UnitOfMeasure.STUECK),
        ("Packung", UnitOfMeasure.PACK),
        ("Palette", UnitOfMeasure.PALETTE),
        ("Rolle", UnitOfMeasure.ROLLE),
        ("kg", UnitOfMeasure.KILOGRAMM),
        ("Liter", UnitOfMeasure.LITER),
        ("m²", UnitOfMeasure.QUADRATMETER),
        ("LFM", UnitOfMeasure.METER),
        (None, UnitOfMeasure.NOT_APPLICABLE),
        ("Blister", UnitOfMeasure.NOT_APPLICABLE),
    ],
)
def test_normalize_unit_of_measure(raw: object, expected: UnitOfMeasure) -> None:
    assert normalize_unit_of_measure(raw) is expected


# ---------------------------------------------------------------------------
# Decimal / percentage / quantity parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19 %", Decimal("0.19")),
        ("19%", Decimal("0.19")),
        ("1.234,56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("2,50 €", Decimal("2.50")),
        ("1.000", Decimal("1000")),
        ("0,125", Decimal("0.125")),
        ("−5,00", Decimal("-5.00")),
        ("2.50", Decimal("2.50")),
        (8.5, Decimal("8.5")),
        (3, Decimal("3")),
    ],
)
def test_normalize_decimal(raw: object, expected: Decimal) -> None:
    assert normalize_decimal(raw) == expected


def test_normalize_decimal_rejects_missing_values() -> None:
    with pytest.raises(NormalizationError):
        normalize_decimal(None)
    with pytest.raises(NormalizationError):
        normalize_decimal("keine Angabe")
    with pytest.raises(NormalizationError):
        normalize_decimal(True)


def test_normalize_percentage() -> None:
    assert normalize_percentage("19") == Decimal("0.19")
    assert normalize_percentage("19%") == Decimal("0.19")
    assert normalize_percentage("0,19") == Decimal("0.19")
    assert normalize_percentage(7) == Decimal("0.07")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Karton à 1000 Stk", 1000),
        ("100 Stück/Pack", 100),
        ("Inhalt: 500 Stk.", 500),
        (250, 250),
        (None, None),
        ("ohne Angabe", None),
    ],
)
def test_normalize_pack_size(raw: object, expected: int | None) -> None:
    assert normalize_pack_size(raw) == expected


def test_normalize_quantity() -> None:
    assert normalize_quantity("1.000") == 1000
    assert normalize_quantity(2) == 2
    with pytest.raises(NormalizationError):
        normalize_quantity("2,5")
    with pytest.raises(NormalizationError):
        normalize_quantity(0)


# ---------------------------------------------------------------------------
# Dictionary-level normalization and strict-schema integration
# ---------------------------------------------------------------------------


def test_normalize_raw_extraction_maps_known_keys_only() -> None:
    raw = {
        "farbe": "Grün",
        "material": "Nitril",
        "glove_size": "Gr. 8",
        "menge": "1.000",
        "mwst": "19%",
        "pack_size": "Karton à 1000 Stk",
        "ean": "4012345678901",
    }

    normalized = normalize_raw_extraction(raw)

    assert normalized["farbe"] is StandardizedColor.GREEN
    assert normalized["material"] is MaterialType.NITRILE
    assert normalized["glove_size"] is GloveSize.M
    assert normalized["menge"] == 1000
    assert normalized["mwst"] == Decimal("0.19")
    assert normalized["pack_size"] == 1000
    assert normalized["ean"] == "4012345678901"


def test_strict_schema_rejects_raw_text_and_accepts_normalized_values() -> None:
    with pytest.raises(ValidationError):
        StrictLineItem(
            glove_size="Gr. 8",  # type: ignore[arg-type]
            color="blau",  # type: ignore[arg-type]
            material="Nitril",  # type: ignore[arg-type]
            unit="Karton",  # type: ignore[arg-type]
        )

    item = StrictLineItem(
        **normalize_raw_extraction(
            {
                "glove_size": "Gr. 8",
                "color": "blau",
                "material": "Nitril",
                "unit": "Karton à 1000 Stk",
            }
        )
    )

    assert item.glove_size is GloveSize.M
    assert item.color is StandardizedColor.BLUE
    assert item.material is MaterialType.NITRILE
    assert item.unit is UnitOfMeasure.KARTON


def test_normalize_raw_extraction_forbids_extra_keys_downstream() -> None:
    normalized = normalize_raw_extraction(
        {
            "glove_size": "8",
            "color": "blau",
            "material": "Nitril",
            "unit": "Karton",
            "unknown_field": "x",
        }
    )

    with pytest.raises(ValidationError):
        StrictLineItem(**normalized)
