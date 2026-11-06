"""Alembic offline-render test: the initial migration must produce valid PostgreSQL SQL."""

from __future__ import annotations

import io
from pathlib import Path

from alembic.config import Config

from alembic import command
from src.config import reset_settings_cache

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def test_initial_migration_renders_offline(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://yasmina:secret@localhost:5432/yasmina")
    reset_settings_cache()

    buffer = io.StringIO()
    config = Config(str(ALEMBIC_INI), output_buffer=buffer)
    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE TABLE tenders" in sql
    assert "CREATE TABLE audit_logs" in sql
    assert "CREATE TABLE integration_idempotency" in sql
    assert "CREATE TABLE workflow_checkpoints" in sql
    assert "CREATE TABLE workflow_checkpoint_blobs" in sql
    assert "CREATE TABLE workflow_checkpoint_writes" in sql
    assert "CREATE TABLE tender_ingestion_jobs" in sql
    assert "CREATE TABLE scraper_states" in sql
    assert "CREATE TABLE worker_runs" in sql
    assert "CREATE TABLE invoice_number_sequences" in sql
    assert "CREATE TABLE invoices" in sql
    assert "CREATE TABLE invoice_lines" in sql
    assert "CREATE TABLE invoice_vat_splits" in sql
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE refresh_tokens" in sql
    assert "CREATE TABLE token_blocklist" in sql
    assert "ALTER TABLE tenders ADD COLUMN owner" in sql
    assert "invoice_artifacts" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "CREATE TRIGGER trg_audit_logs_append_only" in sql
    assert "CREATE TRIGGER trg_audit_logs_no_truncate" in sql
    assert "INSERT INTO alembic_version" in sql
    assert sql.rstrip().endswith("COMMIT;")

    # Every domain table is created exactly once.
    assert sql.count("CREATE TABLE ") == 25  # 24 domain tables + alembic_version

    reset_settings_cache()
