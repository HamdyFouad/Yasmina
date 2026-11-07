"""Catalogue merge planning and duplicate collapsing."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.repositories.catalog_repository import (
    CatalogSeedItem,
    ExistingArticle,
    dedupe_catalog_items,
    merge_article_values,
)


def make_item(**overrides: object) -> CatalogSeedItem:
    values: dict[str, object] = {
        "supplier_name": "Hygiene Gross GmbH",
        "article_number": "NIT-100",
        "description": "Nitrilhandschuhe blau",
        "material": MaterialType.NITRILE,
        "color": StandardizedColor.BLUE,
        "glove_size": GloveSize.M,
        "unit": UnitOfMeasure.KARTON,
        "unit_price_net": Decimal("19.95"),
        "pack_size": 1000,
        "attributes": {"brand": "CleanPro"},
        "embedding": [0.1, 0.2],
        "embedding_model": "BAAI/bge-m3",
    }
    values.update(overrides)
    return CatalogSeedItem(**values)  # type: ignore[arg-type]


def make_existing(**overrides: object) -> ExistingArticle:
    values: dict[str, object] = {
        "id": uuid4(),
        "article_number": "NIT-100",
        "description": "Nitrilhandschuhe blau",
        "material": MaterialType.NITRILE,
        "color": StandardizedColor.BLUE,
        "glove_size": GloveSize.M,
        "unit": UnitOfMeasure.KARTON,
        "unit_price_net": Decimal("18.00"),
        "pack_size": 1000,
        "attributes": {"brand": "CleanPro", "internal_note": "keep me"},
        "manual_overrides": (),
    }
    values.update(overrides)
    return ExistingArticle(**values)  # type: ignore[arg-type]


def test_duplicate_rows_collapse_last_wins() -> None:
    first = make_item(description="old description")
    second = make_item(description="new description")

    unique, duplicates = dedupe_catalog_items([first, second])

    assert duplicates == 1
    assert len(unique) == 1
    assert unique[0].description == "new description"


def test_duplicate_key_is_supplier_and_article_number() -> None:
    other_article = make_item(article_number="NIT-200")
    other_supplier = make_item(supplier_name="Other Supplier")

    unique, duplicates = dedupe_catalog_items([make_item(), other_article, other_supplier])

    assert duplicates == 0
    assert len(unique) == 3


def test_merge_new_article_returns_all_supplied_fields() -> None:
    updates, overrides = merge_article_values(None, make_item())

    assert overrides == ()
    assert updates["description"] == "Nitrilhandschuhe blau"
    assert updates["material"] is MaterialType.NITRILE
    assert updates["unit_price_net"] == Decimal("19.95")
    assert updates["attributes"] == {"brand": "CleanPro"}
    assert updates["embedding"] == [0.1, 0.2]
    assert updates["embedding_model"] == "BAAI/bge-m3"


def test_manual_overrides_win_over_imported_values() -> None:
    existing = make_existing(manual_overrides=("description", "unit_price_net"))
    item = make_item(
        description="imported description",
        unit_price_net=Decimal("25.00"),
        color=StandardizedColor.BLACK,
    )

    updates, overrides = merge_article_values(existing, item)

    assert "description" not in updates
    assert "unit_price_net" not in updates
    assert updates["color"] is StandardizedColor.BLACK
    assert overrides == ("description", "unit_price_net")


def test_import_never_clears_fields_it_does_not_supply() -> None:
    existing = make_existing(
        material=MaterialType.NITRILE,
        color=StandardizedColor.BLUE,
        glove_size=GloveSize.L,
        unit=UnitOfMeasure.KARTON,
        pack_size=1000,
    )
    item = make_item(
        description="updated description",
        material=None,
        color=None,
        glove_size=GloveSize.NOT_APPLICABLE,
        unit=None,
        unit_price_net=None,
        pack_size=None,
        attributes={},
        embedding=None,
        embedding_model=None,
    )

    updates, _ = merge_article_values(existing, item)

    assert updates == {"description": "updated description"}


def test_attributes_are_merged_and_respect_overrides() -> None:
    existing = make_existing(attributes={"brand": "CleanPro", "note": "keep"})
    item = make_item(attributes={"brand": "NewBrand", "colour_code": "17"})

    updates, _ = merge_article_values(existing, item)
    assert updates["attributes"] == {"brand": "NewBrand", "note": "keep", "colour_code": "17"}

    pinned = make_existing(
        attributes={"brand": "ManualBrand"},
        manual_overrides=("attributes",),
    )
    pinned_updates, _ = merge_article_values(pinned, item)
    assert "attributes" not in pinned_updates


def test_ignore_manual_overrides_clears_pins_only_for_written_fields() -> None:
    existing = make_existing(manual_overrides=("description", "pack_size", "color"))
    item = make_item(description="imported", color=StandardizedColor.BLACK, pack_size=None)

    updates, overrides = merge_article_values(existing, item, ignore_manual_overrides=True)

    assert updates["description"] == "imported"
    assert updates["color"] is StandardizedColor.BLACK
    assert "pack_size" not in updates
    assert overrides == ("pack_size",)
