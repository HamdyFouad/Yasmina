"""Administrative CLI for catalogue seeding and operational maintenance."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from src.config import Settings, get_settings
from src.db import dispose_engine, get_session_factory
from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure
from src.parsers.normalizers import (
    normalize_color,
    normalize_glove_size,
    normalize_material,
    normalize_pack_size,
    normalize_unit_of_measure,
)
from src.repositories.catalog_repository import CatalogRepository, CatalogSeedItem
from src.services.embedding_service import (
    EmbeddingError,
    HashingEmbeddingProvider,
    build_embedding_provider,
    embedding_text,
)
from src.services.token_maintenance import TokenCleanupResult, purge_auth_tokens

__all__ = [
    "CatalogSeedError",
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
) -> dict[str, Any]:
    rows = load_catalog_rows(file_path)
    items = parse_catalog_rows(rows, default_supplier=supplier)
    if not items:
        raise CatalogSeedError("catalogue contains no rows")
    if dry_run:
        return {
            "dry_run": True,
            "articles": len(items),
            "suppliers": len({item.supplier_name for item in items}),
        }

    provider = build_embedding_provider(settings, embedding_provider)
    if isinstance(provider, HashingEmbeddingProvider) and not allow_hash_embeddings:
        raise CatalogSeedError(
            "refusing non-semantic hashing embeddings; configure an embedding API key "
            "or pass --allow-hash-embeddings for an explicit offline dry run"
        )
    batch_size = settings.embedding_batch_size
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
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
            for item in batch
        ]
        vectors = await provider.embed(texts)
        for item, vector in zip(batch, vectors, strict=True):
            item.embedding = vector

    factory = get_session_factory(settings)
    try:
        async with factory() as session:
            result = await CatalogRepository(session).seed(items)
    finally:
        await dispose_engine()
    return {
        "dry_run": False,
        "articles": len(items),
        "suppliers": result.suppliers,
        "upserted": result.upserted,
    }


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
    seed.add_argument("--dry-run", action="store_true")
    seed.add_argument("--json", action="store_true", dest="as_json")
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parser().parse_args(argv)
    try:
        if args.command == "seed-catalog":
            summary = asyncio.run(
                seed_catalog(
                    file_path=args.file,
                    settings=get_settings(),
                    supplier=args.supplier,
                    embedding_provider=args.embedding_provider,
                    allow_hash_embeddings=args.allow_hash_embeddings,
                    dry_run=args.dry_run,
                )
            )
            if args.as_json:
                print(json.dumps(summary, indent=2, ensure_ascii=False))
            else:
                print(
                    f"catalogue: {summary['articles']} articles, "
                    f"{summary['suppliers']} suppliers, "
                    f"{summary.get('upserted', 0)} upserted"
                )
            return 0
        if args.command == "purge-auth-tokens":
            cleanup = asyncio.run(
                purge_tokens(
                    settings=get_settings(),
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
    except (CatalogSeedError, EmbeddingError, ValueError) as exc:
        print(f"admin configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - CLI boundary
        logger.exception("admin command failed")
        print(f"admin command failed: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
