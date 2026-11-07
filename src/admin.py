"""Administrative CLI for catalogue seeding and operational maintenance."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.db import dispose_engine, get_session_factory
from src.logging_config import configure_logging
from src.models.enums import AuditEventType, GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.parsers.normalizers import (
    normalize_color,
    normalize_glove_size,
    normalize_material,
    normalize_pack_size,
    normalize_unit_of_measure,
)
from src.repositories.catalog_repository import (
    CatalogRepository,
    CatalogSeedItem,
    dedupe_catalog_items,
)
from src.services.audit_service import AuditService
from src.services.backup_service import (
    BackupError,
    BackupResult,
    generate_backup_key,
    run_postgres_backup,
)
from src.services.embedding_pipeline import EmbeddingBatchProgress, embed_in_batches
from src.services.embedding_service import (
    EmbeddingError,
    EmbeddingProvider,
    HashingEmbeddingProvider,
    build_embedding_provider,
    embedding_text,
)
from src.services.token_maintenance import TokenCleanupResult, purge_auth_tokens

__all__ = [
    "CatalogSeedError",
    "backup_postgres",
    "load_catalog_rows",
    "main",
    "parse_catalog_rows",
    "purge_tokens",
    "seed_catalog",
]

logger = logging.getLogger("yasmina.admin")


class CatalogSeedError(ValueError):
    """Raised when a catalogue file cannot be validated or seeded."""


def load_catalog_rows(path: str | Path) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.is_file():
        raise CatalogSeedError(f"catalogue file not found: {file_path}")
    suffix = file_path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CatalogSeedError(f"cannot read JSON catalogue: {exc}") from exc
        if isinstance(payload, dict):
            payload = payload.get("articles", payload.get("items"))
        if not isinstance(payload, list):
            raise CatalogSeedError("JSON catalogue must be a list or an object with 'articles'")
        if not all(isinstance(row, dict) for row in payload):
            raise CatalogSeedError("every JSON catalogue row must be an object")
        return [dict(row) for row in payload]
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]
        except OSError as exc:
            raise CatalogSeedError(f"cannot read CSV catalogue: {exc}") from exc
    raise CatalogSeedError("catalogue file must end in .json, .csv or .tsv")


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _attributes(row: dict[str, Any]) -> dict[str, Any]:
    known = {
        "supplier",
        "supplier_name",
        "article_number",
        "sku",
        "description",
        "name",
        "material",
        "color",
        "colour",
        "glove_size",
        "size",
        "unit",
        "unit_price_net",
        "unit_price",
        "price",
        "pack_size",
        "attributes",
    }
    raw = row.get("attributes")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise CatalogSeedError(f"attributes must be valid JSON: {exc}") from exc
        attributes = dict(parsed) if isinstance(parsed, dict) else {"value": parsed}
    elif isinstance(raw, dict):
        attributes = dict(raw)
    else:
        attributes = {}
    attributes.update(
        {
            key: value
            for key, value in row.items()
            if key not in known and value is not None and str(value).strip()
        }
    )
    return attributes


def _optional_material(raw: Any) -> MaterialType | None:
    material = normalize_material(str(raw) if raw is not None else None)
    return None if material in {MaterialType.NOT_APPLICABLE, MaterialType.MIXED} else material


def _optional_color(raw: Any) -> StandardizedColor | None:
    color = normalize_color(str(raw) if raw is not None else None)
    return None if color in {StandardizedColor.UNKNOWN, StandardizedColor.PRINTED_MULTICOLOR} else color


def _optional_unit(raw: Any) -> UnitOfMeasure | None:
    unit = normalize_unit_of_measure(str(raw) if raw is not None else None)
    return None if unit is UnitOfMeasure.NOT_APPLICABLE else unit


def _price(raw: Any) -> Decimal | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = Decimal(str(raw).replace(",", "."))
    except InvalidOperation as exc:
        raise CatalogSeedError(f"invalid unit price {raw!r}") from exc
    if value < 0:
        raise CatalogSeedError("unit price must not be negative")
    return value


def parse_catalog_rows(
    rows: list[dict[str, Any]],
    *,
    default_supplier: str | None = None,
) -> list[CatalogSeedItem]:
    items: list[CatalogSeedItem] = []
    for index, row in enumerate(rows, start=1):
        supplier = _first(row, "supplier", "supplier_name") or default_supplier
        article_number = _first(row, "article_number", "sku")
        description = _first(row, "description", "name")
        if not supplier or not str(supplier).strip():
            raise CatalogSeedError(f"row {index}: supplier is required")
        if not article_number or not str(article_number).strip():
            raise CatalogSeedError(f"row {index}: article_number is required")
        if not description or not str(description).strip():
            raise CatalogSeedError(f"row {index}: description is required")
        glove_raw = _first(row, "glove_size", "size")
        pack_raw = _first(row, "pack_size")
        items.append(
            CatalogSeedItem(
                supplier_name=str(supplier).strip()[:256],
                article_number=str(article_number).strip()[:64],
                description=str(description).strip()[:512],
                material=_optional_material(_first(row, "material")),
                color=_optional_color(_first(row, "color", "colour")),
                glove_size=(
                    normalize_glove_size(str(glove_raw))
                    if glove_raw is not None
                    else GloveSize.NOT_APPLICABLE
                ),
                unit=_optional_unit(_first(row, "unit")),
                unit_price_net=_price(_first(row, "unit_price_net", "unit_price", "price")),
                pack_size=(normalize_pack_size(pack_raw) if pack_raw is not None else None),
                attributes=_attributes(row),
            )
        )
    return items


async def seed_catalog(
    *,
    file_path: str | Path,
    settings: Settings,
    supplier: str | None = None,
    embedding_provider: str = "auto",
    allow_hash_embeddings: bool = False,
    dry_run: bool = False,
    ignore_manual_overrides: bool = False,
    progress: Callable[[EmbeddingBatchProgress], None] | None = None,
) -> dict[str, Any]:
    rows = load_catalog_rows(file_path)
    items = parse_catalog_rows(rows, default_supplier=supplier)
    if not items:
        raise CatalogSeedError("catalogue contains no rows")
    unique_items, duplicates = dedupe_catalog_items(items)
    if dry_run:
        return {
            "dry_run": True,
            "articles": len(unique_items),
            "suppliers": len({item.supplier_name for item in unique_items}),
            "duplicates": duplicates,
            "batches": (len(unique_items) + settings.embedding_batch_size - 1)
            // settings.embedding_batch_size,
        }

    provider = build_embedding_provider(settings, embedding_provider)
    if isinstance(provider, HashingEmbeddingProvider) and not allow_hash_embeddings:
        raise CatalogSeedError(
            "refusing non-semantic hashing embeddings; configure an embedding API key "
            "or pass --allow-hash-embeddings for an explicit offline dry run"
        )
    texts = [
        embedding_text(
            description=item.description,
            material=item.material.value if item.material else None,
            color=item.color.value if item.color else None,
            glove_size=item.glove_size.value,
            unit=item.unit.value if item.unit else None,
            pack_size=item.pack_size,
            attributes=item.attributes,
        )
        for item in unique_items
    ]
    run = await embed_in_batches(
        provider,
        texts,
        batch_size=settings.embedding_batch_size,
        max_retries=settings.embedding_max_retries,
        backoff_seconds=settings.embedding_retry_backoff_seconds,
        on_progress=progress or _progress_reporter("embedding"),
    )
    model = _embedding_model_label(settings, provider)
    for item, vector in zip(unique_items, run.vectors, strict=True):
        item.embedding = vector
        item.embedding_model = model

    factory = get_session_factory(settings)
    try:
        async with factory() as session:
            result = await CatalogRepository(session).seed(
                unique_items,
                ignore_manual_overrides=ignore_manual_overrides,
            )
    finally:
        await dispose_engine()
    return {
        "dry_run": False,
        "articles": len(unique_items),
        "suppliers": result.suppliers,
        "created": result.created,
        "updated": result.updated,
        "upserted": result.upserted,
        "duplicates": result.duplicates,
        "batches": run.batches,
        "retries": run.retries,
        "embedding_model": model,
    }


async def reindex_catalog(
    *,
    settings: Settings,
    batch_size: int | None = None,
    only_missing: bool = False,
    dry_run: bool = False,
    embedding_provider: str = "auto",
    allow_hash_embeddings: bool = False,
    respect_overrides: bool = False,
    progress: Callable[[EmbeddingBatchProgress], None] | None = None,
) -> dict[str, Any]:
    """Recompute pgvector embeddings for existing catalogue rows in place."""

    size = max(1, batch_size or settings.embedding_batch_size)
    factory = get_session_factory(settings)
    if dry_run:
        # A dry run only counts candidates; it must not require API credentials.
        try:
            async with factory() as session:
                total = await CatalogRepository(session).count_articles(only_missing=only_missing)
        finally:
            await dispose_engine()
        return {
            "dry_run": True,
            "articles": total,
            "only_missing": only_missing,
            "batch_size": size,
            "embedding_model": _model_label_for_provider(settings, embedding_provider),
        }

    provider = build_embedding_provider(settings, embedding_provider)
    if isinstance(provider, HashingEmbeddingProvider) and not allow_hash_embeddings:
        raise CatalogSeedError(
            "refusing non-semantic hashing embeddings; configure an embedding API key "
            "or pass --allow-hash-embeddings for an explicit offline reindex"
        )
    model = _embedding_model_label(settings, provider)
    processed = 0
    skipped = 0
    batches = 0
    retries = 0
    try:
        async with factory() as session:
            repository = CatalogRepository(session)
            total = await repository.count_articles(only_missing=only_missing)

            after_id = None
            reporter = progress or _progress_reporter("reindex")
            while True:
                page = await repository.reindex_page(
                    after_id=after_id,
                    limit=size,
                    only_missing=only_missing,
                )
                if not page:
                    break
                after_id = page[-1].id
                candidates = [
                    article
                    for article in page
                    if not (respect_overrides and "embedding" in article.manual_overrides)
                ]
                skipped += len(page) - len(candidates)
                if not candidates:
                    continue
                texts = [
                    embedding_text(
                        description=article.description,
                        material=article.material.value if article.material else None,
                        color=article.color.value if article.color else None,
                        glove_size=article.glove_size.value,
                        unit=article.unit.value if article.unit else None,
                        pack_size=article.pack_size,
                        attributes=article.attributes,
                    )
                    for article in candidates
                ]
                run = await embed_in_batches(
                    provider,
                    texts,
                    batch_size=size,
                    max_retries=settings.embedding_max_retries,
                    backoff_seconds=settings.embedding_retry_backoff_seconds,
                    on_progress=reporter,
                )
                rows = [
                    (article.id, vector, model)
                    for article, vector in zip(candidates, run.vectors, strict=True)
                ]
                await repository.update_embeddings(rows)
                processed += len(rows)
                batches += run.batches
                retries += run.retries
                logger.info(
                    "catalog reindex progress",
                    extra={
                        "event": "catalog_reindex_progress",
                        "processed": processed,
                        "total": total,
                    },
                )
            await _audit_reindex(
                session,
                processed=processed,
                total=total,
                model=model,
                only_missing=only_missing,
                dry_run=False,
            )
    finally:
        await dispose_engine()
    return {
        "dry_run": False,
        "articles": total,
        "processed": processed,
        "skipped": skipped,
        "batches": batches,
        "retries": retries,
        "embedding_model": model,
        "only_missing": only_missing,
    }


async def _audit_reindex(
    session: Any,
    *,
    processed: int,
    total: int,
    model: str,
    only_missing: bool,
    dry_run: bool,
) -> None:
    try:
        await AuditService(session).append(
            event_type=AuditEventType.SYSTEM_CATALOG_REINDEXED,
            actor="yasmina-admin",
            entity_type="catalog_articles",
            entity_id="reindex",
            payload={
                "processed": processed,
                "total": total,
                "embedding_model": model,
                "only_missing": only_missing,
                "dry_run": dry_run,
            },
        )
    except Exception:  # pragma: no cover - embeddings are already persisted
        logger.exception("catalogue reindex audit entry could not be recorded")


def _embedding_model_label(settings: Settings, provider: EmbeddingProvider) -> str:
    if isinstance(provider, HashingEmbeddingProvider):
        return f"hashing-{provider.dimensions}"
    return settings.embedding_model


def _model_label_for_provider(settings: Settings, provider: str) -> str:
    normalized = provider.strip().lower()
    has_key = bool(
        (settings.embedding_api_key and settings.embedding_api_key.get_secret_value().strip())
        or (settings.deepseek_api_key and settings.deepseek_api_key.get_secret_value().strip())
    )
    if normalized in {"hashing", "hash"} or (normalized == "auto" and not has_key):
        return f"hashing-{settings.embedding_dimensions}"
    return settings.embedding_model


def _progress_reporter(label: str) -> Callable[[EmbeddingBatchProgress], None]:
    def report(progress: EmbeddingBatchProgress) -> None:
        message = f"{label}: {progress.completed}/{progress.total} ({progress.percent:.0f}%)"
        if sys.stderr.isatty():
            print(f"\r{message}", end="", file=sys.stderr, flush=True)
            if progress.completed >= progress.total:
                print(file=sys.stderr)
            return
        logger.info(
            message,
            extra={
                "event": f"{label}_progress",
                "completed": progress.completed,
                "total": progress.total,
                "batch": progress.batch_index,
                "batches": progress.batch_count,
            },
        )

    return report


async def purge_tokens(
    *,
    settings: Settings,
    retention_days: int | None = None,
    dry_run: bool = False,
) -> TokenCleanupResult:
    """Drop expired refresh tokens, blocklisted JTIs and reset tokens."""

    factory = get_session_factory(settings)
    try:
        async with factory() as session:
            return await purge_auth_tokens(
                session,
                retention_days=(
                    settings.auth_token_retention_days if retention_days is None else retention_days
                ),
                dry_run=dry_run,
            )
    finally:
        await dispose_engine()


async def backup_postgres(
    *,
    settings: Settings,
    dry_run: bool = False,
    retention_days: int | None = None,
) -> BackupResult:
    """Encrypt a pg_dump archive, upload it to MinIO and prune old backups."""

    if retention_days is not None:
        settings = settings.model_copy(
            update={"backup_retention_days": max(0, retention_days)},
        )
    try:
        result = await run_postgres_backup(settings, dry_run=dry_run)
        if not dry_run:
            await _audit_backup(settings, result)
        return result
    finally:
        await dispose_engine()


async def _audit_backup(settings: Settings, result: BackupResult) -> None:
    factory = get_session_factory(settings)
    try:
        async with factory() as session:
            await AuditService(session).append(
                event_type=AuditEventType.SYSTEM_BACKUP_COMPLETED,
                actor="yasmina-admin",
                entity_type="database_backup",
                entity_id=result.key[:64],
                payload={
                    "bucket": result.bucket,
                    "key": result.key,
                    "size_bytes": result.size_bytes,
                    "sha256": result.sha256,
                    "pruned": list(result.pruned),
                },
            )
    except Exception:  # pragma: no cover - the uploaded backup is the primary artifact
        logger.exception("backup audit entry could not be recorded")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yasmina-admin", description="Project Yasmina administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed = subparsers.add_parser("seed-catalog", help="upsert supplier catalogue + embeddings")
    seed.add_argument("--file", required=True, help="CSV, TSV or JSON catalogue path")
    seed.add_argument("--supplier", default=None, help="default supplier when rows omit one")
    seed.add_argument(
        "--embedding-provider",
        choices=("auto", "openai", "deepseek", "local", "hashing"),
        default="auto",
    )
    seed.add_argument(
        "--allow-hash-embeddings",
        action="store_true",
        help="explicitly allow deterministic offline hashing vectors",
    )
    seed.add_argument(
        "--ignore-manual-overrides",
        action="store_true",
        help="let imported values win over pinned manual overrides",
    )
    seed.add_argument("--dry-run", action="store_true")
    seed.add_argument("--json", action="store_true", dest="as_json")
    reindex = subparsers.add_parser(
        "reindex-catalog",
        help="recompute pgvector embeddings for existing catalogue articles",
    )
    reindex.add_argument(
        "--embedding-provider",
        choices=("auto", "openai", "deepseek", "local", "hashing"),
        default="auto",
    )
    reindex.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="articles per embedding batch (default: EMBEDDING_BATCH_SIZE)",
    )
    reindex.add_argument(
        "--only-missing",
        action="store_true",
        help="only backfill rows whose embedding is NULL",
    )
    reindex.add_argument(
        "--respect-overrides",
        action="store_true",
        help="skip articles whose embedding field is manually pinned",
    )
    reindex.add_argument(
        "--allow-hash-embeddings",
        action="store_true",
        help="explicitly allow deterministic offline hashing vectors",
    )
    reindex.add_argument("--dry-run", action="store_true", help="count candidates without embedding")
    reindex.add_argument("--json", action="store_true", dest="as_json")
    purge = subparsers.add_parser(
        "purge-auth-tokens",
        help="delete expired refresh tokens, access-token JTIs and reset tokens",
    )
    purge.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="keep revoked refresh tokens this many days (default: AUTH_TOKEN_RETENTION_DAYS)",
    )
    purge.add_argument("--dry-run", action="store_true", help="count rows without deleting")
    purge.add_argument("--json", action="store_true", dest="as_json")
    backup = subparsers.add_parser(
        "backup-postgres",
        help="encrypt a pg_dump archive, upload it to MinIO and prune old backups",
    )
    backup.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="keep backups this many days (default: BACKUP_RETENTION_DAYS)",
    )
    backup.add_argument("--dry-run", action="store_true", help="show the target key without dumping")
    backup.add_argument("--json", action="store_true", dest="as_json")
    key = subparsers.add_parser(
        "generate-backup-key",
        help="print a fresh Fernet key for BACKUP_ENCRYPTION_KEY",
    )
    key.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
    except Exception as exc:
        configure_logging("INFO", json_format=False)
        print(f"admin configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, json_format=settings.log_format == "json")
    try:
        if args.command == "seed-catalog":
            summary = asyncio.run(
                seed_catalog(
                    file_path=args.file,
                    settings=settings,
                    supplier=args.supplier,
                    embedding_provider=args.embedding_provider,
                    allow_hash_embeddings=args.allow_hash_embeddings,
                    dry_run=args.dry_run,
                    ignore_manual_overrides=args.ignore_manual_overrides,
                )
            )
            if args.as_json:
                print(json.dumps(summary, indent=2, ensure_ascii=False))
            else:
                print(
                    f"catalogue: {summary['articles']} articles, "
                    f"{summary['suppliers']} suppliers, "
                    f"{summary.get('created', 0)} created, "
                    f"{summary.get('updated', 0)} updated, "
                    f"{summary.get('duplicates', 0)} duplicates collapsed"
                )
            return 0
        if args.command == "reindex-catalog":
            reindex_summary = asyncio.run(
                reindex_catalog(
                    settings=settings,
                    batch_size=args.batch_size,
                    only_missing=args.only_missing,
                    dry_run=args.dry_run,
                    embedding_provider=args.embedding_provider,
                    allow_hash_embeddings=args.allow_hash_embeddings,
                    respect_overrides=args.respect_overrides,
                )
            )
            if args.as_json:
                print(json.dumps(reindex_summary, indent=2, ensure_ascii=False))
            else:
                if reindex_summary["dry_run"]:
                    print(
                        f"reindex: would embed {reindex_summary['articles']} articles "
                        f"with {reindex_summary['embedding_model']}"
                    )
                else:
                    print(
                        f"reindex: {reindex_summary['processed']}/{reindex_summary['articles']} articles "
                        f"re-embedded with {reindex_summary['embedding_model']}, "
                        f"{reindex_summary['skipped']} skipped"
                    )
            return 0
        if args.command == "purge-auth-tokens":
            cleanup = asyncio.run(
                purge_tokens(
                    settings=settings,
                    retention_days=args.retention_days,
                    dry_run=args.dry_run,
                )
            )
            if args.as_json:
                print(json.dumps(cleanup.as_dict(), indent=2, ensure_ascii=False))
            else:
                prefix = "would purge" if cleanup.dry_run else "purged"
                print(
                    f"{prefix}: {cleanup.refresh_tokens} refresh tokens, "
                    f"{cleanup.blocklisted_jtis} blocklisted JTIs, "
                    f"{cleanup.password_reset_tokens} password reset tokens "
                    f"(retention {cleanup.retention_days} days)"
                )
            return 0
        if args.command == "backup-postgres":
            backup = asyncio.run(
                backup_postgres(
                    settings=settings,
                    dry_run=args.dry_run,
                    retention_days=args.retention_days,
                )
            )
            if args.as_json:
                print(json.dumps(backup.as_dict(), indent=2, ensure_ascii=False))
            else:
                action = "would create" if backup.dry_run else "uploaded"
                print(
                    f"{action} {backup.key} ({backup.size_bytes} bytes) to bucket {backup.bucket}; "
                    f"pruned {len(backup.pruned)} old backup(s)"
                )
            return 0
        if args.command == "generate-backup-key":
            key = generate_backup_key()
            if args.as_json:
                print(json.dumps({"backup_encryption_key": key}, indent=2))
            else:
                print(key)
            return 0
    except (BackupError, CatalogSeedError, EmbeddingError, ValueError) as exc:
        print(f"admin configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - CLI boundary
        logger.exception("admin command failed")
        print(f"admin command failed: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
