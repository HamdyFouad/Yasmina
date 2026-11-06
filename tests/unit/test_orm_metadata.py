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
    "integration_idempotency",
    "workflow_checkpoints",
    "workflow_checkpoint_blobs",
    "workflow_checkpoint_writes",
    "tender_ingestion_jobs",
    "scraper_states",
    "worker_runs",
    "invoice_number_sequences",
    "invoices",
    "invoice_lines",
    "invoice_vat_splits",
    "users",
    "refresh_tokens",
    "token_blocklist",
}


def test_metadata_contains_all_domain_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_all_tables_and_indexes_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl
        for index in table.indexes:
            index_ddl = str(CreateIndex(index).compile(dialect=dialect))
            assert "CREATE INDEX" in index_ddl or "CREATE UNIQUE INDEX" in index_ddl


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


def test_integration_idempotency_shape() -> None:
    table = Base.metadata.tables["integration_idempotency"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert table.c.key.primary_key is True
    assert table.c.request_hash.type.length == 64
    assert table.c.response.nullable is True
    assert {
        "ck_integration_idempotency_status_valid",
        "ck_integration_idempotency_request_hash_format",
    } <= constraint_names


def test_offer_persistence_and_workflow_checkpoint_columns() -> None:
    offers = Base.metadata.tables["customer_offers"]
    checkpoints = Base.metadata.tables["workflow_checkpoints"]
    blobs = Base.metadata.tables["workflow_checkpoint_blobs"]
    writes = Base.metadata.tables["workflow_checkpoint_writes"]

    assert offers.c.owner.type.length == 128
    assert offers.c.invoice_artifacts.nullable is False
    assert "ix_customer_offers_owner" in {index.name for index in offers.indexes}
    assert checkpoints.c.checkpoint_blob.type.__class__.__name__ == "LargeBinary"
    assert {"thread_id", "checkpoint_ns", "checkpoint_id"} <= {
        column.name for column in checkpoints.primary_key.columns
    }
    assert {"thread_id", "checkpoint_ns", "channel", "version"} == {
        column.name for column in blobs.primary_key.columns
    }
    assert {"thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"} == {
        column.name for column in writes.primary_key.columns
    }


def test_ingestion_outbox_and_watermark_shape() -> None:
    jobs = Base.metadata.tables["tender_ingestion_jobs"]
    states = Base.metadata.tables["scraper_states"]

    assert jobs.c.tender_id.unique is True
    assert jobs.c.attachments.nullable is False
    assert {
        "ck_tender_ingestion_jobs_status_valid",
        "ck_tender_ingestion_jobs_attempts_non_negative",
    } <= {constraint.name for constraint in jobs.constraints}
    assert "ix_tender_ingestion_jobs_status_available" in {index.name for index in jobs.indexes}
    assert states.c.portal_name.primary_key is True
    assert states.c.last_success_at.nullable is True


def test_worker_run_history_shape() -> None:
    runs = Base.metadata.tables["worker_runs"]

    assert {
        "ck_worker_runs_run_type_valid",
        "ck_worker_runs_status_valid",
        "ck_worker_runs_items_non_negative",
        "ck_worker_runs_errors_non_negative",
    } <= {constraint.name for constraint in runs.constraints}
    assert runs.c.details.nullable is False
    assert runs.c.duration_ms.nullable is True
    assert {"ix_worker_runs_started_at", "ix_worker_runs_type_status"} == {
        index.name for index in runs.indexes
    }


def test_invoice_and_vat_tables_shape() -> None:
    invoices = Base.metadata.tables["invoices"]
    lines = Base.metadata.tables["invoice_lines"]
    splits = Base.metadata.tables["invoice_vat_splits"]
    sequences = Base.metadata.tables["invoice_number_sequences"]

    assert invoices.c.offer_id.unique is True
    assert invoices.c.vat_breakdown.nullable is False
    assert invoices.c.status.nullable is False
    assert {
        "ck_invoices_invoice_number_format",
        "ck_invoices_totals_consistent",
        "ck_invoices_payment_plus_credit_bounded",
    } <= {constraint.name for constraint in invoices.constraints}
    assert {"uq_invoice_year_sequence"} <= {constraint.name for constraint in invoices.constraints}
    assert lines.c.vat_rate.nullable is False
    assert "uq_invoice_line" in {constraint.name for constraint in lines.constraints}
    assert "uq_invoice_vat_rate" in {constraint.name for constraint in splits.constraints}
    assert sequences.c.last_value.nullable is False
    assert sequences.c.year.primary_key is True


def test_customer_offer_line_has_vat_rate() -> None:
    lines = Base.metadata.tables["customer_offer_lines"]

    assert lines.c.vat_rate.nullable is False
    assert "ck_customer_offer_lines_vat_rate_range" in {constraint.name for constraint in lines.constraints}


def test_identity_and_refresh_token_tables_shape() -> None:
    users = Base.metadata.tables["users"]
    refresh_tokens = Base.metadata.tables["refresh_tokens"]
    blocklist = Base.metadata.tables["token_blocklist"]
    tenders = Base.metadata.tables["tenders"]

    assert users.c.username.unique is True
    assert users.c.email.unique is True
    assert users.c.roles.nullable is False
    assert users.c.password_hash.type.length == 512
    assert "ck_users_failed_attempts_non_negative" in {constraint.name for constraint in users.constraints}
    assert refresh_tokens.c.token_hash.unique is True
    assert refresh_tokens.c.token_hash.type.length == 64
    assert "ck_refresh_tokens_token_hash_format" in {
        constraint.name for constraint in refresh_tokens.constraints
    }
    assert blocklist.c.jti.primary_key is True
    assert "ix_token_blocklist_expires" in {index.name for index in blocklist.indexes}
    assert tenders.c.owner.nullable is True
    assert "ix_tenders_owner" in {index.name for index in tenders.indexes}
