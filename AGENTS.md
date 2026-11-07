# Project Yasmina — Core Instructions & Agent Guidelines

## 1. Project Identity & Scope
**Project Yasmina** is an enterprise-grade, AI-orchestrated B2B trading, sourcing, and public/private tender bidding engine tailored for the German cleaning and hygiene supplies sector (paper towels, nitrile/latex gloves, garbage and waste bags across all thicknesses, volumes, and color standards).

## 2. Mandatory Architecture & References
- **Master Engineering Specification:** You **MUST** read and strictly follow all definitions, Enums, Pydantic schemas, SQL filters, pricing rules, and compliance requirements defined in **`docs/DETAILED_SPEC.md`**.
- **Isolation of External Services:** Do **NOT** clone external ERP or automation repos. External systems (`ERPNext`, `n8n`, `PostgreSQL/pgvector`, `MinIO`) run as isolated Docker containers (`docker-compose.yml`) and communicate solely via REST APIs, signed Webhooks, or SQL connections.
- **Strict Separation of AI vs. Deterministic Logic:** LLMs (DeepSeek via OpenAI-compatible `ChatOpenAI`) are restricted to extraction, matching assistance, and summarization. All financial calculations, margins, tiered volume discounts, MOQ rounding, and VAT math must be executed by the 100% deterministic Python pricing engine (`src/services/pricing_service.py`) using `decimal.Decimal`.
- **Pre-Validation Normalization:** Raw extracted German strings (e.g., `"Nitril"`, `"Gr. 8"`, `"blau"`) must pass through `src/parsers/normalizers.py` to map into strict Pydantic Enums **before** schema validation.
- **Human-in-the-Loop (HITL) Governance:** No ERPNext Quotation creation or GAEB `.x84` bid export may execute without a valid Ed25519 cryptographic human approval signature (`CustomerOffer.ed25519_approval_signature`).

## 3. Tech Stack & Key Libraries
- **Language:** Python 3.11+ / FastAPI / Pydantic v2 (`strict=True`, `extra="forbid"`).
- **Parsers:** `pygaeb` (`.x83`, `.d83`, `.x84`), `Docling` (PDF tables).
- **Database & Search:** SQLAlchemy 2.0 Async, `asyncpg`, Alembic migrations, `pgvector` (HNSW index).
- **Workflow & AI:** LangGraph, PostgreSQL SQLAlchemy checkpoint saver, `langchain-openai` (DeepSeek-Chat / DeepSeek-Reasoner).
- **E-Invoicing:** `factur-x` (ZUGFeRD 2.2), custom XRechnung 3.0 / EN 16931 UBL generator, official KoSIT CIUS Schematron via `saxonche`, branded PDF/A-3 carrier invoice.
- **Identity & Security:** Argon2id password hashing, short-lived JWT access tokens with `jti`, rotating hashed refresh-token families, PostgreSQL JTI blocklist, four-eyes approval and in-memory token-bucket rate limits.

## 4. Directory Structure Enforcement
Maintain the exact directory layout defined in `docs/DETAILED_SPEC.md` (Section 10):
- `src/config.py` (pydantic-settings)
- `src/db.py` (async SQLAlchemy session factory)
- `src/admin.py` (`yasmina-admin` catalogue seeding, token retention and encrypted-backup CLI)
- `src/main.py` (FastAPI app + startup preflight lifespan)
- `src/logging_config.py` (JSON log formatter and handler configuration)
- `src/api/` (`security.py`, `rate_limit.py`, `observability.py`, `metrics.py`, `deps.py`, `schemas.py`, `routes/tenders.py`, `routes/approvals.py`, `routes/offers.py`, `routes/invoices.py`, `routes/ops.py`, `routes/auth.py`)
- `src/repositories/` (`tender_repository.py`, `offer_repository.py`, `invoice_repository.py`, `credit_note_repository.py`, `invoice_event_repository.py`, `user_repository.py`, `catalog_repository.py`, `ingestion_repository.py`, `worker_run_repository.py`, `integration_repository.py`, `supplier_quote_repository.py`)
- `src/models/` (`enums.py`, `schemas.py`, `orm.py`)
- `src/parsers/` (`normalizers.py`, `pdf_parser.py`, `gaeb_parser.py`, `gaeb_validator.py`)
- `src/services/` (`matching_service.py`, `pricing_service.py`, `approval_service.py`, `unit_converter.py`, `password_service.py`, `mfa_service.py`, `token_maintenance.py`, `backup_service.py`, `embedding_service.py`, `embedding_pipeline.py`, `ingestion_processor.py`, `worker_lock.py`, `worker_metrics.py`)
- `src/agents/` (`graph.py`, `checkpoint.py`, `nodes.py`, `state.py`, `deepseek.py`)
- `src/integrations/` (`clamav_client.py`, `minio_client.py`, `erpnext_client.py`, `n8n_client.py`, `telegram_notifier.py`; Telegram approval bot planned)
- `src/scrapers/` (`base_scraper.py`, `bayern_vergabe.py`, `oeffentlichevergabe.py`, `ingestion.py`)
- `src/scheduler.py` and `src/worker.py` (cron scheduler, scrape/ingestion CLI, advisory locks and shutdown signals)
- `src/generators/` (`invoice_models.py`, `leitweg.py`, `pdf_template.py`, `fonts/`, `schematron/`, `zugferd_generator.py`, `xrechnung_generator.py`)
- `tests/` (unit, integration, evals with German golden fixtures)

## 5. Execution Protocol for Coding Agents
When given an implementation task:
1. **Read `docs/DETAILED_SPEC.md`** relevant to the task section first.
2. Implement code adhering strictly to the typed schemas, naming conventions, and decimal precision rules.
3. Verify that unit tests pass and that strict mode validation does not encounter un-normalized string errors.

## 6. Build, Run & Verification Commands
- Setup: `python -m pip install -e ".[dev,parsing,ai,e-invoicing]"`; `.env` is created from `.env.example` on first preflight/API start (copy it manually for other tooling).
- Tests: `python -m pytest -q` (config lives in `pyproject.toml`); live DB tests use `YASMINA_TEST_DATABASE_URL`.
- Quality: `python -m ruff check .`, `python -m ruff format --check .`, `python -m mypy src`.
- Catalogue: `python -m src.admin seed-catalog --file catalog.csv [--embedding-provider auto|local|hashing] [--dry-run] [--ignore-manual-overrides]`; re-imports merge by `(supplier, article_number)`, preserve pinned `catalog_articles.manual_overrides`, never clear omitted fields and collapse duplicate rows (last wins).
- Catalogue reindex: `python -m src.admin reindex-catalog [--batch-size N] [--only-missing] [--respect-overrides] [--dry-run] [--json] [--embedding-provider …] [--allow-hash-embeddings]`; recomputes pgvector embeddings in place, never deletes rows, commits per page and audits `SYSTEM_CATALOG_REINDEXED`.
- Embeddings: seeding/reindex share `src/services/embedding_pipeline.py` (batch size `EMBEDDING_BATCH_SIZE`, retries `EMBEDDING_MAX_RETRIES` with exponential backoff honouring `Retry-After` for 429/5xx, progress on stderr so `--json` stdout stays clean).
- Extraction eval: `python -m pytest -q tests/evals/test_extraction_eval.py -s` (live accuracy gate when `DEEPSEEK_API_KEY` is set).
- API: `python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload` (startup runs preflight).
- Infrastructure: `docker compose up -d postgres minio clamav` (add `--profile automation` for n8n).
- Preflight: `python -m src.preflight` (or `python -m src.preflight --json`).
- Approval: `yasmina-approval sign|verify` (see `src/services/approval_service.py`).
- Auth: `POST /api/v1/auth/token` (access + refresh; `totp_code` when MFA is enabled), `/auth/refresh` (rotating), `/auth/logout` (JTI blocklist), `/auth/users` (admin). Four-eyes rejects self-approval by owner/tender creator.
- Account lifecycle: `POST /auth/password/change` (current password required), `/auth/password/reset` (single-use token), `/auth/password/reset/request` (public, always 202), `/auth/users/{username}/password/reset` (admin-issued token), `/auth/logout/all` (revoke every refresh session).
- MFA: `/auth/mfa/setup` returns a base32 secret + `otpauth://` URI, `/auth/mfa/verify` enables TOTP enrollment, `/auth/mfa/disable` needs password + live code; secrets are Fernet-sealed from `APP_SECRET_KEY` and `users.mfa_last_step` blocks code replay. RFC 6238 code lives in `src/services/mfa_service.py`.
- Token retention: `python -m src.admin purge-auth-tokens [--retention-days N] [--dry-run] [--json]` purges expired refresh tokens, stale revoked sessions, expired JTIs and used/expired reset tokens, and appends a `SYSTEM_TOKEN_CLEANUP` audit entry.
- Auth audit: `AuditService` records `USER_LOGIN_SUCCEEDED|FAILED`, `USER_LOGOUT`, `USER_LOGGED_OUT_ALL_DEVICES`, `USER_CREATED`, `USER_PASSWORD_CHANGED`, `USER_PASSWORD_RESET_REQUESTED|COMPLETED`, `USER_MFA_ENABLED|DISABLED`; never include passwords, reset tokens or TOTP secrets.
- Rate limits: `/auth` 20/min, `/upload` 30/min, other `/api/v1` 300/min by default; adjust `RATE_LIMIT_*` settings.
- Observability: `LOG_FORMAT=json` emits structured JSON (request id, method, route, status, duration); `GET /metrics` exposes `yasmina_http_*` and `yasmina_workflow_*` Prometheus metrics; middlewares live in `src/api/observability.py` and `src/api/metrics.py`.
- Production image: `docker build -t yasmina-api:prod .` (multi-stage, non-root UID 10001, precompiled bytecode); `docker build --build-arg INSTALL_EXTRAS="ai,e-invoicing" -t yasmina-api:slim .` skips Docling/pyGAEB.
- Production stack: `cp .env.prod.example .env.prod` then `docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build`; add `--profile backup` for the daily encrypted-backup sidecar. API on loopback:8000, services on the internal `backend` network.
- Backups: `python -m src.admin generate-backup-key` writes `BACKUP_ENCRYPTION_KEY`; `python -m src.admin backup-postgres [--dry-run] [--retention-days N] [--json]` runs Fernet-encrypted `pg_dump` uploads to `BACKUP_BUCKET` and appends `SYSTEM_BACKUP_COMPLETED`.
- E-invoicing: `XRechnungGenerator` runs KoSIT EN16931 + XRechnung CIUS Schematron via `saxonche`; `ZUGFeRDGenerator` embeds CII XML in the branded PDF/A-3 carrier; `InvoiceRepository` owns gapless `INV-YYYY-XXXX` numbering and multi-rate VAT rows.
- Billing: `GET /api/v1/invoices[/{id}]` (owner-scoped for sales), `POST /invoices/{id}/payments` and `POST /invoices/{id}/credits` (admin-only); `CreditNoteRepository` allocates a gapless `GS-YYYY-XXXX` credit number across the invoice VAT buckets and applies it atomically.
- GoBD event log: `InvoiceEventRepository` appends hash-chained `invoice_events` for issuance/payment/credit/status changes; PostgreSQL triggers block UPDATE/DELETE/TRUNCATE, and `verify_chain()` re-derives every hash.
- PDF/A-3 gate: `python scripts/generate_sample_invoice.py sample-invoice.pdf` then `docker run --rm -v "$PWD:/data" verapdf/cli:latest -f 3b /data/sample-invoice.pdf`; the report must contain `isCompliant="true"`. The carrier embeds DejaVu TrueType faces from `src/generators/fonts/`.
- Scrape worker: `python -m src.worker scrape|schedule` (daily cron via `SCRAPER_SCHEDULE_CRON`).
- Ingestion worker: `python -m src.worker process [--limit N] [--interval SECONDS]` drains `tender_ingestion_jobs` with `DeepSeek` triage and Docling/GAEB parsing.
- Worker operations: portal locks use `pg_try_advisory_lock`; SIGINT/SIGTERM finish in-flight work; `GET /healthz/worker` exposes `worker_runs` history and outbox counts; Telegram alerts use `TELEGRAM_BOT_TOKEN` + `TELEGRAM_APPROVAL_CHAT_ID`.
- Migrations: `python -m alembic upgrade head`; offline SQL preview: `python -m alembic upgrade head --sql`.
- Spec gaps and inferred schema fields are tracked in `docs/ASSUMPTIONS.md`.