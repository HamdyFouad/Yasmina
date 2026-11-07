"""Live PostgreSQL catalogue upsert and pgvector embedding tests."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.admin import reindex_catalog, seed_catalog
from src.config import Settings
from src.db import dispose_engine, get_session_factory
from src.models.enums import AuditEventType, MaterialType, StandardizedColor, UnitOfMeasure
from src.models.orm import AuditLog, CatalogArticle, Supplier
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
        embedding_dimensions=1024,
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


async def test_reimport_updates_prices_without_clobbering_manual_overrides() -> None:
    factory = get_session_factory(settings())
    supplier_name = f"p46-{uuid4().hex[:10]}"
    try:
        async with factory() as session:
            await _cleanup(session)
            repository = CatalogRepository(session)
            base = CatalogSeedItem(
                supplier_name=supplier_name,
                article_number="NIT-MERGE",
                description="Nitrilhandschuhe blau (Original)",
                material=MaterialType.NITRILE,
                color=StandardizedColor.BLUE,
                unit=UnitOfMeasure.KARTON,
                unit_price_net=Decimal("10.00"),
                attributes={"brand": "Original", "note": "keep"},
                embedding=[0.1] * 1024,
                embedding_model="BAAI/bge-m3",
            )
            first = await repository.seed([base])
            assert first.created == 1
            assert first.updated == 0

            supplier = (
                (await session.execute(select(Supplier).where(Supplier.name == supplier_name)))
                .scalars()
                .one()
            )
            article = (
                (
                    await session.execute(
                        select(CatalogArticle).where(CatalogArticle.supplier_id == supplier.id)
                    )
                )
                .scalars()
                .one()
            )
            await repository.set_manual_overrides(article.id, ["description"])
            article_id = article.id

        async with factory() as session:
            repository = CatalogRepository(session)
            item = CatalogSeedItem(
                supplier_name=supplier_name,
                article_number="NIT-MERGE",
                description="Nitrilhandschuhe blau (Import)",
                color=StandardizedColor.BLACK,
                unit_price_net=Decimal("12.50"),
                attributes={"brand": "Imported"},
                embedding=[0.2] * 1024,
                embedding_model="BAAI/bge-m3",
            )
            second = await repository.seed([item])
            assert second.created == 0
            assert second.updated == 1

        async with factory() as session:
            article = await session.get(CatalogArticle, article_id)
            assert article is not None
            assert article.description == "Nitrilhandschuhe blau (Original)"
            assert article.unit_price_net == Decimal("12.50")
            assert article.color is StandardizedColor.BLACK
            assert article.attributes == {"brand": "Imported", "note": "keep"}
            assert list(article.manual_overrides) == ["description"]
            assert article.last_imported_at is not None
            assert article.embedding_model == "BAAI/bge-m3"
            assert len(article.embedding) == 1024

        async with factory() as session:
            repository = CatalogRepository(session)
            item.description = "Nitrilhandschuhe blau (Forced)"
            forced = await repository.seed([item], ignore_manual_overrides=True)
            assert forced.updated == 1

        async with factory() as session:
            article = await session.get(CatalogArticle, article_id)
            assert article is not None
            assert article.description == "Nitrilhandschuhe blau (Forced)"
            assert list(article.manual_overrides) == []
            rows = (
                (await session.execute(select(CatalogArticle).where(CatalogArticle.id == article_id)))
                .scalars()
                .all()
            )
            assert len(rows) == 1
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_duplicate_import_rows_collapse_into_one_article() -> None:
    factory = get_session_factory(settings())
    supplier_name = f"p46-{uuid4().hex[:10]}"
    try:
        async with factory() as session:
            await _cleanup(session)
            repository = CatalogRepository(session)
            items = [
                CatalogSeedItem(
                    supplier_name=supplier_name,
                    article_number="PAP-DUP",
                    description="Papierhandtücher (first)",
                ),
                CatalogSeedItem(
                    supplier_name=supplier_name,
                    article_number="PAP-DUP",
                    description="Papierhandtücher (second)",
                ),
            ]
            result = await repository.seed(items)
            assert result.duplicates == 1
            assert result.created == 1
            assert result.upserted == 1

        async with factory() as session:
            supplier = (
                (await session.execute(select(Supplier).where(Supplier.name == supplier_name)))
                .scalars()
                .one()
            )
            articles = (
                (
                    await session.execute(
                        select(CatalogArticle).where(CatalogArticle.supplier_id == supplier.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(articles) == 1
            assert articles[0].description == "Papierhandtücher (second)"
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_admin_seed_catalog_merges_a_reimported_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    current_settings = settings()
    supplier_name = f"p46-{uuid4().hex[:10]}"
    catalogue = tmp_path / "catalog.csv"
    catalogue.write_text(
        "supplier,article_number,description,unit_price_net,material,color,unit,pack_size\n"
        f"{supplier_name},ADM-1,Papierhandtücher weiß,9.99,Papier,weiß,Karton,20\n"
        f"{supplier_name},ADM-2,Nitrilhandschuhe blau,19.95,Nitril,blau,Karton,1000\n",
        encoding="utf-8",
    )
    try:
        first = await seed_catalog(
            file_path=catalogue,
            settings=current_settings,
            embedding_provider="hashing",
            allow_hash_embeddings=True,
        )
        assert first["created"] == 2
        assert first["updated"] == 0
        assert first["embedding_model"] == "hashing-1024"

        catalogue.write_text(
            "supplier,article_number,description,unit_price_net,material,color,unit,pack_size\n"
            f"{supplier_name},ADM-1,Papierhandtücher weiß (neu),11.49,Papier,weiß,Karton,20\n"
            f"{supplier_name},ADM-2,Nitrilhandschuhe blau,21.00,Nitril,blau,Karton,1000\n",
            encoding="utf-8",
        )
        second = await seed_catalog(
            file_path=catalogue,
            settings=current_settings,
            embedding_provider="hashing",
            allow_hash_embeddings=True,
        )
        assert second["created"] == 0
        assert second["updated"] == 2

        factory = get_session_factory(current_settings)
        async with factory() as session:
            supplier = (
                (await session.execute(select(Supplier).where(Supplier.name == supplier_name)))
                .scalars()
                .one()
            )
            articles = (
                (
                    await session.execute(
                        select(CatalogArticle).where(CatalogArticle.supplier_id == supplier.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(articles) == 2
            by_number = {article.article_number: article for article in articles}
            assert by_number["ADM-1"].description == "Papierhandtücher weiß (neu)"
            assert by_number["ADM-1"].unit_price_net == Decimal("11.49")
            assert by_number["ADM-2"].unit_price_net == Decimal("21.00")
            assert by_number["ADM-1"].embedding_model == "hashing-1024"
    finally:
        factory = get_session_factory(current_settings)
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()


async def test_reindex_catalog_backfills_embeddings_without_deleting_rows() -> None:
    current_settings = settings()
    factory = get_session_factory(current_settings)
    supplier_name = f"p46-{uuid4().hex[:10]}"
    try:
        async with factory() as session:
            await _cleanup(session)
            repository = CatalogRepository(session)
            items = [
                CatalogSeedItem(
                    supplier_name=supplier_name,
                    article_number=f"RE-{index}",
                    description=description,
                    material=MaterialType.PAPER,
                    embedding=[0.1] * 1024,
                )
                for index, description in enumerate(
                    ["Papierhandtücher weiß", "Müllsäcke blau", "Nitrilhandschuhe schwarz"],
                    start=1,
                )
            ]
            await repository.seed(items)
            before = await repository.count_articles()

        dry_run = await reindex_catalog(
            settings=current_settings,
            embedding_provider="hashing",
            allow_hash_embeddings=True,
            dry_run=True,
        )
        assert dry_run["dry_run"] is True
        assert dry_run["articles"] >= 3

        summary = await reindex_catalog(
            settings=current_settings,
            embedding_provider="hashing",
            allow_hash_embeddings=True,
            batch_size=2,
        )
        assert summary["processed"] >= 3
        assert summary["embedding_model"] == "hashing-1024"
        assert summary["batches"] >= 2

        async with factory() as session:
            after = await CatalogRepository(session).count_articles()
            supplier = (
                (await session.execute(select(Supplier).where(Supplier.name == supplier_name)))
                .scalars()
                .one()
            )
            articles = (
                (
                    await session.execute(
                        select(CatalogArticle).where(CatalogArticle.supplier_id == supplier.id)
                    )
                )
                .scalars()
                .all()
            )
            assert after == before
            assert len(articles) == 3
            for article in articles:
                assert list(article.embedding) != [0.1] * 1024
                assert article.embedding_model == "hashing-1024"
            audit = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.event_type == AuditEventType.SYSTEM_CATALOG_REINDEXED.value
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert audit
    finally:
        async with factory() as session:
            await _cleanup(session)
        await dispose_engine()
