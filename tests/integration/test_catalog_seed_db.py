"""Live PostgreSQL catalogue upsert and pgvector embedding tests."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.models.enums import MaterialType, StandardizedColor, UnitOfMeasure
from src.models.orm import CatalogArticle, Supplier
from src.repositories.catalog_repository import CatalogRepository, CatalogSeedItem
from src.services.embedding_service import HashingEmbeddingProvider

DATABASE_URL = os.getenv("YASMINA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set YASMINA_TEST_DATABASE_URL to run PostgreSQL integration tests",
)


def settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        app_env="test",
        jwt_secret_key="live-test-jwt-secret-with-at-least-32-bytes",
        api_admin_password="test-admin-password",
    )


async def _cleanup(session) -> None:  # type: ignore[no-untyped-def]
    suppliers = (await session.execute(select(Supplier).where(Supplier.name.like("p46-%")))).scalars().all()
    for supplier in suppliers:
        await session.execute(delete(CatalogArticle).where(CatalogArticle.supplier_id == supplier.id))
        await session.execute(delete(Supplier).where(Supplier.id == supplier.id))
    await session.commit()


async def test_catalog_seed_upserts_articles_and_embeddings() -> None:
    factory = get_session_factory(settings())
    supplier_name = f"p46-{uuid4().hex[:10]}"
    provider = HashingEmbeddingProvider(1024)
    vectors = await provider.embed(["Nitrilhandschuhe blau", "Papierhandtücher"])
    try:
        async with factory() as session:
            await _cleanup(session)
            repository = CatalogRepository(session)
            items = [
                CatalogSeedItem(
                    supplier_name=supplier_name,
                    article_number="NIT-1",
                    description="Nitrilhandschuhe blau",
                    material=MaterialType.NITRILE,
                    color=StandardizedColor.BLUE,
                    unit=UnitOfMeasure.KARTON,
                    unit_price_net=Decimal("19.95"),
                    pack_size=1000,
                    embedding=vectors[0],
                ),
                CatalogSeedItem(
                    supplier_name=supplier_name,
                    article_number="PAP-1",
                    description="Papierhandtücher",
                    material=MaterialType.PAPER,
                    embedding=vectors[1],
                ),
            ]
            result = await repository.seed(items)
            assert result.upserted == 2
            assert result.suppliers == 1
            assert await repository.count() >= 2

            items[0].description = "Nitrilhandschuhe blau (aktualisiert)"
            items[0].unit_price_net = Decimal("21.50")
            second = await repository.seed(items)
            assert second.upserted == 2

        async with factory() as session:
            supplier = (
                (await session.execute(select(Supplier).where(Supplier.name == supplier_name)))
                .scalars()
                .one()
            )
            articles = (
                (
                    await session.execute(
                        select(CatalogArticle)
                        .where(CatalogArticle.supplier_id == supplier.id)
                        .order_by(CatalogArticle.article_number)
                    )
                )
                .scalars()
                .all()
            )
            assert [article.article_number for article in articles] == ["NIT-1", "PAP-1"]
            assert articles[0].description.endswith("(aktualisiert)")
            assert articles[0].unit_price_net == Decimal("21.50")
            assert articles[0].embedding is not None
            assert len(articles[0].embedding) == 1024
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()
