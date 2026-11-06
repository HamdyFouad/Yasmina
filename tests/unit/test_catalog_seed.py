"""Catalogue parsing, embedding and seed-CLI tests."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.admin import CatalogSeedError, load_catalog_rows, parse_catalog_rows, seed_catalog
from src.config import Settings
from src.models.enums import MaterialType, StandardizedColor, UnitOfMeasure
from src.services.embedding_service import (
    HashingEmbeddingProvider,
    embedding_text,
)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        app_env="test",
        jwt_secret_key="test-catalogue-jwt-secret-with-32-bytes",
        api_admin_password="test-admin-password",
        embedding_dimensions=32,
    )


def test_parse_csv_like_rows_normalizes_german_values() -> None:
    items = parse_catalog_rows(
        [
            {
                "supplier": "Hygiene Gross GmbH",
                "sku": "NIT-100",
                "name": "Nitrilhandschuhe blau Gr. M",
                "material": "Nitril",
                "colour": "blau",
                "size": "M",
                "unit": "Karton à 1000 Stk",
                "price": "19,95",
                "pack_size": "1000",
                "brand": "CleanPro",
            }
        ]
    )

    assert len(items) == 1
    item = items[0]
    assert item.article_number == "NIT-100"
    assert item.material is MaterialType.NITRILE
    assert item.color is StandardizedColor.BLUE
    assert item.unit is UnitOfMeasure.KARTON
    assert item.unit_price_net == Decimal("19.95")
    assert item.pack_size == 1000
    assert item.attributes == {"brand": "CleanPro"}


def test_load_json_and_dry_run_without_database(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    path = workspace_tmp / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "articles": [
                    {
                        "supplier": "Hygiene Gross GmbH",
                        "article_number": "PAP-1",
                        "description": "Papierhandtücher 2-lagig",
                        "material": "Papier",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = load_catalog_rows(path)
    items = parse_catalog_rows(rows)
    assert items[0].material is MaterialType.PAPER

    import asyncio

    summary = asyncio.run(
        seed_catalog(
            file_path=path,
            settings=make_settings(),
            dry_run=True,
        )
    )
    assert summary["dry_run"] is True
    assert summary["articles"] == 1


def test_hashing_embedding_is_deterministic_and_normalized() -> None:
    import asyncio

    provider = HashingEmbeddingProvider(16)
    first = asyncio.run(provider.embed(["Nitrilhandschuhe blau"]))[0]
    second = asyncio.run(provider.embed(["Nitrilhandschuhe blau"]))[0]

    assert first == second
    assert len(first) == 16
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9
    assert embedding_text(description="Gloves", color="blue").startswith("Gloves | blue")


def test_invalid_catalogue_rows_are_rejected() -> None:
    with pytest.raises(CatalogSeedError, match="article_number"):
        parse_catalog_rows([{"supplier": "S", "description": "X"}])
    with pytest.raises(CatalogSeedError, match="description"):
        parse_catalog_rows([{"supplier": "S", "article_number": "A"}])
    with pytest.raises(CatalogSeedError, match="supplier"):
        parse_catalog_rows([{"article_number": "A", "description": "X"}])
