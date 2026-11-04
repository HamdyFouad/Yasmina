"""ORM metadata tests: PostgreSQL DDL, pgvector index, audit log shape."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from src.models.orm import AuditLog, Base, CatalogArticle

EXPECTED_TABLES = {
    "suppliers",
    "tenders",
    "tender_items",
    "supplier_quotes",
    "supplier_quote_lines",
    "supplier_quote_tiers",
    "customer_offers",
    "customer_offer_lines",
    "catalog_articles",
    "audit_logs",
}


def test_metadata_contains_all_domain_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_all_tables_and_indexes_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl
        for index in table.indexes:
            assert "CREATE INDEX" in str(CreateIndex(index).compile(dialect=dialect))


def test_identifiers_fit_postgresql_limit() -> None:
    names = []
    for table in Base.metadata.tables.values():
        names.extend(constraint.name for constraint in table.constraints if constraint.name)
        names.extend(index.name for index in table.indexes if index.name)

    too_long = [name for name in names if len(name) > 63]
    assert too_long == []


def test_catalog_embedding_is_pgvector_with_hnsw() -> None:
    table = CatalogArticle.__table__
    embedding = table.c.embedding

    assert embedding.type.dim == 1024
    hnsw = next(index for index in table.indexes if index.name == "ix_catalog_articles_embedding_hnsw")
    options = hnsw.dialect_options["postgresql"]

    assert options["using"] == "hnsw"
    assert options["ops"] == {"embedding": "vector_cosine_ops"}
    assert options["with"] == {"m": 16, "ef_construction": 64}


def test_audit_log_is_hash_chained_and_constrained() -> None:
    table = AuditLog.__table__
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "uq_audit_logs_entry_hash" in constraint_names
    assert "ck_audit_logs_entry_hash_format" in constraint_names
    assert "ck_audit_logs_prev_hash_format" in constraint_names
    assert "ck_audit_logs_signature_format" in constraint_names
    assert table.c.prev_hash.nullable is True
    assert table.c.entry_hash.nullable is False
    assert {index.name for index in table.indexes} == {
        "ix_audit_logs_entity",
        "ix_audit_logs_occurred_at",
    }


def test_offer_approval_constraints_exist() -> None:
    table = Base.metadata.tables["customer_offers"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert {
        "ck_customer_offers_approval_required",
        "ck_customer_offers_approval_required_for_export",
        "ck_customer_offers_totals_consistent",
        "ck_customer_offers_approval_signature_format",
        "ck_customer_offers_approval_hash_format",
    } <= constraint_names


def test_hitl_signature_column_is_wide_enough_for_ed25519() -> None:
    signature = Base.metadata.tables["customer_offers"].c.ed25519_approval_signature

    assert signature.type.length == 128  # 64-byte signature, lowercase hex
