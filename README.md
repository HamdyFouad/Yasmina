# Project Yasmina

[![CI](https://github.com/HamdyFouad/Yasmina/actions/workflows/ci.yml/badge.svg)](https://github.com/HamdyFouad/Yasmina/actions/workflows/ci.yml)

**AI-orchestrated B2B sourcing, pricing and tender-bidding engine for the German
cleaning & hygiene supplies sector** — paper towels, nitrile/latex gloves, and
waste bags across all thicknesses, volumes and colour standards.

> **Status:** P0 foundation, P1 core, durable PostgreSQL tender/offer/invoice
> persistence, MinIO object storage, ClamAV-scanned/audited uploads, Ed25519
> approval with automatic XRechnung/ZUGFeRD generation, KoSIT Schematron
> validation, gapless `INV-YYYY-XXXX` numbering, branded PDF/A-3 carrier
> invoices with multi-rate VAT, user identity with Argon2id, short-lived access
> tokens, rotating refresh sessions, token revocation, four-eyes approval,
> API rate limiting, CI quality gates with real PostgreSQL/MinIO/ClamAV,
> pgvector catalogue seeding, AI extraction evals, ERPNext quotation sync,
> HMAC-authenticated n8n RFQ/quote flows, compliant portal scrapers with
> attachment harvesting and durable ingestion outbox, live DeepSeek/Docling
> paths, deterministic unit conversion, PostgreSQL LangGraph checkpoints,
> the production-hardened scrape/ingestion worker (advisory locks, graceful
> shutdown, run metrics and Telegram alerts), a billing REST API with payments
> and credit notes, embedded-font PDF/A-3 output gated by veraPDF, and a
> GoBD-compliant append-only invoice event log are complete.

---

## What it does

Yasmina ingests public and private tenders (GAEB `.x83`/`.d83`/`.x84`, PDF tables,
and portal notices), normalizes messy German source text such as `"Nitril"`,
`"Gr. 8"`, `"19 %"` or `"Karton à 1000 Stk"`, matches positions against supplier
catalogues, prices offers deterministically, and prepares ERPNext quotations /
GAEB bids and ZUGFeRD/XRechnung invoices.

Core principles:

- **LLMs extract, Python decides.** DeepSeek models are limited to extraction,
  matching assistance and summarization. All margins, tiered discounts, MOQ
  rounding, VAT and totals run in deterministic `Decimal` code.
- **Normalize before validation.** Every Pydantic model is
  `strict=True, extra="forbid"`; raw extraction text must pass through
  `src/parsers/normalizers.py` first.
- **Scan uploads first.** ClamAV is provisioned as the mandatory virus-scanning
  gate before any parser or LLM sees an uploaded document.
- **Human-in-the-loop is cryptographic.** An offer cannot be approved, exported
  to GAEB, or pushed to ERPNext without an Ed25519 human approval signature.
- **Audit everything.** `audit_logs` is append-only and hash-chained; the
  database rejects `UPDATE`, `DELETE` and `TRUNCATE` on it.

### Architecture

```text
 Docling / pygaeb / LLM
          │
          ▼
 src/parsers/normalizers.py ──► strict Pydantic schemas ──► deterministic services
          │                         (src/models)                (src/services)
          │                                                        │
          ▼                                                        ▼
 PostgreSQL + pgvector  ◄── audit_logs (SHA-256 chain)      ERPNext / GAEB / ZUGFeRD
```

---

## Repository layout

```text
src/
  config.py              # pydantic-settings configuration
  db.py                  # async SQLAlchemy engine/session factory
  main.py                # FastAPI app + startup preflight lifespan
  admin.py               # yasmina-admin catalogue seeding CLI
  preflight.py           # startup checks (config, DB, MinIO, ClamAV, n8n)
  api/
    security.py          # JWT jti issuance/validation + RBAC roles
    rate_limit.py        # token-bucket auth/upload/business rate limiter
    deps.py              # auth, DB, ClamAV and repository dependencies
    schemas.py           # API request DTOs and payload parsers
    routes/
      auth.py            # login, refresh, logout, users
      tenders.py         # upload (scanned), owner-scoped match/price/workflow
      approvals.py       # four-eyes approval, offer persistence, ERPNext + invoices
      offers.py          # owner-aware offer retrieval
      invoices.py        # invoice list/detail, payments, credit notes, event chain
      integrations.py    # n8n RFQ dispatch + signed quote callback
      ops.py             # /healthz/worker run history + outbox metrics
  repositories/
    tender_repository.py # PostgreSQL tender/position persistence + owner
    offer_repository.py  # offer lines, artifacts and ERPNext names
    invoice_repository.py # gapless invoice numbers, VAT splits, payment/credit status
    credit_note_repository.py # gapless GS-YYYY-XXXX credit notes + VAT allocation
    invoice_event_repository.py # GoBD append-only hash-chained invoice events
    user_repository.py   # users, Argon2id auth, refresh rotation, JTI blocklist
    catalog_repository.py # supplier catalogue + embedding upserts
    ingestion_repository.py # outbox jobs + scraper watermarks
    worker_run_repository.py # run history for scrapes and ingestion batches
    integration_repository.py # durable idempotency-key ledger
    supplier_quote_repository.py # inbound n8n quote persistence
  models/
    enums.py             # canonical StrEnums
    schemas.py           # strict Pydantic v2 domain models
    orm.py               # SQLAlchemy 2.0 mappings + pgvector
  parsers/
    normalizers.py       # German text → enums / Decimal
    gaeb_parser.py       # pyGAEB .x83/.d83 reader, approved .x84 writer
    gaeb_validator.py    # XSD + penny reconciliation
    pdf_parser.py        # Docling LV table extraction
  services/
    pricing_service.py   # pure-Decimal tiers, MOQ, margin, VAT
    unit_converter.py    # auditable CARTON/PACK/PIECE/ROLL conversions
    password_service.py  # Argon2id hashing and password policy
    embedding_service.py # OpenAI-compatible / local / hashing embeddings
    ingestion_processor.py # PDF/GAEB parsing + triage worker
    worker_lock.py       # PostgreSQL advisory locks per portal
    worker_metrics.py    # worker health snapshot aggregation
    matching_service.py  # pgvector retrieval + lexical/embedding ranking
    approval_service.py  # canonical payload, Ed25519 sign/verify/apply
  scheduler.py           # dependency-free five-field cron scheduler
  worker.py              # scrape/process CLI, signals, locks, alerts
  agents/
    graph.py             # LangGraph workflow and routing
    checkpoint.py        # SQLAlchemy-backed checkpoint saver
    nodes.py             # enrich, match, price, approval gate, export
    state.py             # typed state, dependencies and offer request
    deepseek.py          # DeepSeek structured extraction client
  integrations/          # ClamAV, MinIO, ERPNext, n8n and Telegram clients
  scrapers/
    base_scraper.py      # robots.txt, rate limit, UA rotation, MinIO raw archive
    html_portal.py       # generic listing/detail card parser + CPV filtering
    bayern_vergabe.py    # vergabe.bayern.de scraper
    oeffentlichevergabe.py # oeffentlichevergabe.de scraper
    ingestion.py         # TenderRepository persistence + pipeline hand-off
    models.py            # DiscoveredTender, CPV/date normalization
  generators/
    invoice_models.py    # shared invoice/party/line models
    leitweg.py           # Leitweg-ID format + MOD-97-10 validation
    pdf_template.py      # branded PDF/A-3 carrier with embedded TrueType fonts
    fonts/               # DejaVu Sans/Bold TrueType faces + license
    schematron/          # vendored KoSIT EN16931 + XRechnung CIUS rules
    zugferd_generator.py # Factur-X/ZUGFeRD 2.2 CII + PDF embedding
    xrechnung_generator.py # XRechnung 3.0 UBL XML + KoSIT validation
alembic/                 # async migrations (… 0008 identity/security, 0009 billing events)
docker/                  # PostgreSQL bootstrap (pgvector, n8n database)
docs/
  DETAILED_SPEC.md       # authoritative engineering specification
  ASSUMPTIONS.md         # inferred fields / open spec questions
scripts/
  generate_sample_invoice.py # representative invoice PDF for the veraPDF CI gate
tests/                   # unit tests (no database required)
  integration/           # live PostgreSQL/MinIO/identity/catalogue tests
  evals/                 # DeepSeek extraction golden dataset + accuracy gate
```

---

## Quickstart

### Prerequisites

- Python **3.11+**
- Docker Desktop (for PostgreSQL, MinIO and ClamAV; tests do not need it)
- ClamAV needs roughly 2 GB of RAM and downloads its signature database on the
  first container start.

### 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,parsing,ai,e-invoicing]"
```

`parsing` installs Docling for live PDF table extraction; `ai` installs the
LangChain OpenAI adapter used by the real DeepSeek client. Both are optional for
the deterministic core, but required for the corresponding live execution path.

### 2. Configure

The first preflight or API startup copies `.env.example` to `.env` when the file
is missing (local/test only; production must inject environment variables).
Copying manually is optional:

```powershell
Copy-Item .env.example .env
```

Replace every `change-me` placeholder before using shared infrastructure. `.env`
is git-ignored and must never be committed.

### 3. Start infrastructure

```powershell
docker compose up -d postgres minio clamav
# optional automation stack:
# docker compose --profile automation up -d
```

The PostgreSQL container enables the `vector` extension and creates a separate
`n8n` database on first start. MinIO's upstream registries no longer allow
anonymous pulls, so Compose pins the `pgsty/minio` mirror of the official
release; review that pin when upgrading. ERPNext is intentionally **not** part
of this compose stack — it runs as an isolated external system.

### 4. Apply migrations

```powershell
python -m alembic upgrade head
python -m alembic current
```

Without a database you can inspect the exact SQL:

```powershell
python -m alembic upgrade head --sql
```

### 5. Run preflight checks

```powershell
python -m src.preflight
python -m src.preflight --json
python -m src.preflight --skip n8n --skip clamav
```

The preflight verifies configuration guardrails, the writable export directory,
PostgreSQL connectivity plus schema (pgvector, Alembic revision, audit triggers,
HNSW index), MinIO authentication and bucket access, the ClamAV daemon and n8n
reachability. It exits non-zero on a failed required check; `n8n` only warns
unless `--require-n8n` is passed. The installed console script is
`yasmina-preflight`.

### 6. Run the API

```powershell
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
```

The FastAPI lifespan runs the full preflight before accepting requests. If
PostgreSQL, pgvector, MinIO authentication or ClamAV fail, startup aborts with a
`Startup preflight failed` error.

- `GET /healthz` — process liveness
- `GET /healthz/worker` — latest scrape/ingestion run, duration, item/error counts and outbox queue state
- `GET /readyz` — startup preflight results
- `GET /docs` — OpenAPI UI

### 7. Business API

All business routes live under `/api/v1` and require a JWT bearer token.

| Method | Path | Role |
|---|---|---|
| POST | `/auth/token` | username/password login (admin bootstrap allowed) |
| POST | `/auth/refresh` | rotating refresh token |
| POST | `/auth/logout` | authenticated user; revokes access JTI and refresh token |
| GET | `/auth/me` | any authenticated role |
| POST | `/auth/users` | admin only |
| POST | `/tenders/upload` | sales/admin; sets tender owner |
| POST | `/tenders/{id}/match` | sales (own), admin |
| POST | `/tenders/{id}/price` | sales (own), admin |
| POST | `/tenders/{id}/workflow` | sales (own), admin |
| POST | `/approvals/verify` | viewer/approver/admin |
| POST | `/approvals/approve` | approver/admin, four-eyes enforced |
| GET | `/approvals/{offer_number}/documents/{format}` | viewer/approver/admin |
| GET | `/offers` | sales (own), viewer/admin (all) |
| GET | `/offers/{id}` | sales (own), viewer/admin (all) |
| GET | `/invoices` | sales (own), viewer/admin (all) |
| GET | `/invoices/{id}` | sales (own), viewer/admin (all) |
| GET | `/invoices/{id}/events` | sales (own), viewer/admin (all) |
| GET | `/invoices/{id}/credit-notes` | sales (own), viewer/admin (all) |
| POST | `/invoices/{id}/payments` | admin only |
| POST | `/invoices/{id}/credits` | admin only |
| POST | `/integrations/rfq/dispatch` | sales/admin |
| POST | `/integrations/n8n/supplier-quotes` | n8n HMAC signature |

```powershell
$login = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/auth/token `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"change-me-admin-password"}'
$token = $login.access_token
$refresh = $login.refresh_token

$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/tenders/upload `
  -Headers $headers -Form @{ file = Get-Item .\tender.x83 }
```

`/auth/refresh` rotates the refresh token and invalidates the previous one. Reusing
a rotated token revokes the entire token family. `/auth/logout` adds the current
access-token `jti` to `token_blocklist` and optionally revokes the supplied
refresh token. Approval of an offer whose owner is the authenticated principal
(or whose signed `approved_by` equals the owner) is rejected with `403` by the
four-eyes rule. Rate limits return `429` with `Retry-After` for `/auth` (20/min),
`/tenders/upload` (30/min) and other `/api/v1` routes (300/min) by default.

The matching route retrieves candidates directly from `catalog_articles` using
pgvector cosine distance. The workflow route runs the LangGraph state machine;
it returns `pending_approval` until a signed offer is supplied, then
`approved`. Uploads are ClamAV-scanned (INSTREAM) before parsing and fail closed
if the scanner is unavailable; every scan outcome is written to the hash-chained
audit log. Raw uploads are stored in MinIO, while tenders and positions are
persisted in PostgreSQL through `TenderRepository`.

Approving an offer with an `invoice` block generates both XRechnung XML and
ZUGFeRD PDF and stores them under `offers/{offer_number}/` in MinIO. The invoice
row receives a transactionally allocated `INV-YYYY-XXXX` number, line-level VAT
rates, persisted VAT splits and payment/credit status. The download endpoint
serves `format=xrechnung|zugferd`. If ERPNext credentials are configured,
approval also creates (or returns) the matching ERPNext Quotation and stores its
name on the offer.

Approval is now idempotent by `offer_number`: the approved state is persisted
before invoice/ERP side effects, retries return the same offer and artifacts,
and reusing an offer number with a different signature/payload returns `409`.
Workflow runs persist their generated drafts as well, so the pending offer can
be resumed and approved after a worker restart. The optional `owner` field in
the approval payload assigns the sales owner (default: the signed offer's
owner, then the authenticated approver).
`GET /offers` and `GET /offers/{id}` expose durable offers; sales users are
limited to their own `owner`, while viewers and admins can read all. LangGraph
checkpoints are stored in PostgreSQL (`workflow_checkpoint*`) with the workflow
thread key `tender:<id>:<offer_number>`, so a worker restart can resume the
state machine.

#### Billing, credit notes and GoBD events

- `GET /invoices` pages and filters issued invoices; `GET /invoices/{id}` returns
  the invoice with its lifecycle events and an `event_chain_valid` flag. Sales
  users only see invoices they own; viewers and admins see everything.
- `POST /invoices/{id}/payments` records a payment against the invoice. The
  invoice row is locked with `SELECT … FOR UPDATE`, the amount is validated
  against the outstanding balance, and the status moves to `PARTIALLY_PAID` or
  `PAID` in the same transaction.
- `POST /invoices/{id}/credits` issues a credit note with its own gapless
  `GS-YYYY-XXXX` sequence (a separate row-locked yearly counter, not the invoice
  `INV-` series) and applies it to the invoice: the credited amount is allocated
  across the invoice VAT buckets and the status moves to `PARTIALLY_CREDITED` or
  `CREDITED`. `GET /invoices/{id}/credit-notes` lists the issued notes.
- Every issuance, payment, credit, status change and cancellation appends a row
  to `invoice_events`. Rows carry a sequence number, event type, actor, amount,
  reference and payload, and are chained with SHA-256 over canonical JSON
  (`prev_hash` → `entry_hash`), mirroring the `audit_logs` design. PostgreSQL
  triggers reject `UPDATE`, `DELETE` and `TRUNCATE`, so the log is append-only
  and GoBD-compliant; `GET /invoices/{id}` re-verifies the chain on read.

#### ERPNext and n8n integration

- `ERPNextClient` synchronizes Items by `item_code` and Suppliers by
  `supplier_name`; quotations are looked up by the deterministic
  `Yasmina <offer-number> | <customer>` title before creation. Transient HTTP
  429/5xx responses are retried with exponential backoff.
- `POST /integrations/rfq/dispatch` requires an `Idempotency-Key` header. The
  key, request hash and cached response are persisted in
  `integration_idempotency`, so a replay returns the original result without
  sending a second webhook.
- `POST /integrations/n8n/supplier-quotes` is called by n8n (no JWT). Every
  request must carry `X-Yasmina-Timestamp`, `X-Yasmina-Signature:
  sha256=HMAC_SHA256(secret, "<timestamp>." + raw_body)` and an
  `Idempotency-Key`. Signatures outside `N8N_CALLBACK_MAX_AGE_SECONDS` are
  rejected, and quote ingestion is transactional with the idempotency row.

```powershell
$body = '{"tender_id":"...","suppliers":[{"name":"Hygiene Gross GmbH","email":"sales@example.com"}]}'
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/integrations/rfq/dispatch `
  -Headers @{ Authorization = "Bearer $token"; "Idempotency-Key" = "rfq-2026-0001" } `
  -ContentType "application/json" -Body $body
```

### Portal scrapers (Bayern & Bund)

`src/scrapers/` discovers public notices from
`vergabe.bayern.de` and `oeffentlichevergabe.de` with the same guardrails:

- `robots.txt` is fetched and cached per origin/User-Agent; 401/403/429/5xx
  fail closed, while a normal 4xx (including the portals' 404 robots.txt)
  means no restrictions.
- Requests are spaced per host and raised to the robots `Crawl-delay`.
- A rotating User-Agent pool is used; every raw response is archived to MinIO
  under `scrapers/<portal>/<date>/<sha256>.html` before parsing.
- Listings are filtered strictly against `33760000-5`, `19520000-7`,
  `33141420-0` and `39830000-9`. Cards missing a CPV/deadline are enriched from
  the detail page before the filter is applied.
- Detail pages are harvested for GAEB/PDF/ZIP attachments
  (`SCRAPER_HARVEST_ATTACHMENTS`, on by default). Each file is downloaded with
  the same robots/rate-limit/User-Agent guardrails, capped at
  `SCRAPER_MAX_ATTACHMENTS` files of `SCRAPER_MAX_ATTACHMENT_MB` MiB, and
  archived to MinIO under
  `scrapers/<portal>/attachments/<sha256>.<ext>`.
- `PortalIngestionBridge` persists mapped metadata through `TenderRepository`
  (idempotent by tender number). With the outbox enabled it commits the tender
  and one `TenderIngestionJob` in a single transaction instead of calling the
  pipeline inline.

```python
from src.scrapers import BayernVergabeScraper, PortalIngestionBridge

scraper = BayernVergabeScraper(settings, storage)
bridge = PortalIngestionBridge(repository, pipeline=pipeline, audit=audit)
report, results = await bridge.ingest_scraper(scraper, max_pages=2)
```

The Bayern scraper follows the publication iframe used by
`vergabe.bayern.de`; the federal scraper accepts an HTML search-result URL
through `start_url` (the production UI is JavaScript-driven). Incomplete
metadata is skipped rather than guessed.

#### Durable ingestion outbox

Attachment metadata, MinIO object keys and the tender row are written
transactionally; the worker owns the expensive DeepSeek triage and Docling
parsing that follows:

- `tender_ingestion_jobs` has one row per `tender_id` (`unique`), with an
  attempt counter, `available_at` backoff, `locked_at`/`locked_by` leases and a
  `last_error` column. Statuses are `pending`, `processing`, `completed`,
  `skipped` and `failed`.
- `TenderIngestionJobRepository.claim_pending()` uses
  `SELECT ... FOR UPDATE SKIP LOCKED`, so several workers can drain the queue
  without double-processing. A `processing` row whose lease is older than 900
  seconds is reclaimed after a crash.
- `TenderIngestionProcessor` downloads the archived PDF/GAEB bytes, runs the
  default PDF parser (`Docling`) or GAEB parser, deduplicates positions, runs
  DeepSeek triage plus spec enrichment and replaces the tender's line items.
  Transient failures retry with exponential backoff up to `max_attempts`;
  documents with no extractable positions finish as `skipped`.
- `scraper_states` stores a per-portal watermark: `last_scraped_at` is written
  when a run starts and `last_success_at` when it completes. The next run passes
  that timestamp as `since`, so listings are only re-fetched from the watermark
  onward while rate limits still apply.

#### Daily scrape worker

`yasmina-worker` (or `python -m src.worker`) runs `ingest_scraper()` once or on a
five-field cron schedule in `APP_TIMEZONE`, then drains the ingestion outbox:

```powershell
# one immediate run, optionally only one portal
python -m src.worker scrape --portal bayern --max-pages 2

# long-running daily scheduler (default SCRAPER_SCHEDULE_CRON=0 2 * * *)
python -m src.worker schedule --portal all

# drain queued ingestion jobs once or continuously
python -m src.worker process --limit 20
python -m src.worker process --interval 30
```

The scheduler is dependency-free and logs job failures without terminating.
Each scheduled scrape is followed by one `process_jobs_once()` pass, so a single
cron entry keeps both discovery and parsing moving.

#### Production hardening and observability

- **Distributed portal locks:** every worker opens a dedicated connection and
  takes `pg_try_advisory_lock(<stable portal key>)` before scraping a portal. A
  second worker gets `False`, records the portal as `skipped` and moves on; the
  lock is released in `finally` (and by PostgreSQL if the connection dies).
- **Graceful shutdown:** SIGINT/SIGTERM set a cooperative stop event. The
  current scrape portal and current DeepSeek/Docling job finish, their locks are
  released, unstarted claims return to `pending`, and the process exits without
  cancelling an in-flight API call.
- **Run history:** `worker_runs` records `run_type`, portal/resource, status,
  start/finish times, duration, processed items, error counts and details for
  each scrape and each ingestion batch. `GET /healthz/worker` returns the latest
  run per type, status counts, outbox queue counts and the ten most recent runs;
  it returns 503 only when the metrics query itself fails.
- **Telegram alerts:** set `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_APPROVAL_CHAT_ID` to receive alerts after a scheduled scrape
  exception or when an ingestion job exhausts `max_attempts` and reaches the
  dead-letter `failed` state. Delivery is best-effort and never breaks the
  worker.

### 8. Run tests

```powershell
python -m pytest -q
```

The current 323 tests pass (plus seventeen optional tests — fourteen live
PostgreSQL tests, two MinIO/ClamAV smoke tests and the DeepSeek eval — that are
skipped unless their environment variables are set) and cover normalizers, settings, strict schemas,
ORM metadata, pgvector/HNSW configuration, GAEB parsing/validation, PDF table
extraction, pricing, unit conversion, hybrid matching, LangGraph
triage/enrichment and checkpointing, Ed25519 approval, offer
persistence/idempotency, gapless invoice and credit-note numbering, multi-rate
VAT, KoSIT Schematron validation, embedded-font PDF/A-3 invoices, the
append-only invoice event chain, billing/payment/credit API routes, Argon2id
users, refresh-token rotation/reuse detection, JWT blocklisting, four-eyes
approval, tender/offer ownership, rate limiting, catalogue CSV/JSON seeding and
vector upserts, deepseek extraction evals, durable ingestion outbox
claiming/retry,
attachment harvesting and watermarking, advisory locking, signal-driven
shutdown, worker run metrics, Telegram delivery, ClamAV scanning and audit
chaining, MinIO object flows, ERPNext retries/payload mapping, n8n HMAC
callbacks and idempotency, portal robots/rate/UA/artifact handling,
CPV-filtered HTML extraction, cron scheduling, FastAPI business routes, RBAC,
tender persistence, e-invoicing and offline migration rendering.

```powershell
# optional live PostgreSQL offer/checkpoint, outbox/lock, invoice, identity,
# catalogue and billing tests (after alembic upgrade head)
$env:YASMINA_TEST_DATABASE_URL = "postgresql+asyncpg://yasmina:yasmina@localhost:5432/yasmina"
python -m pytest -q tests/integration/test_offer_workflow_db.py tests/integration/test_ingestion_outbox_db.py tests/integration/test_invoices_db.py tests/integration/test_identity_db.py tests/integration/test_catalog_seed_db.py tests/integration/test_billing_db.py
# with local MinIO/ClamAV:
$env:YASMINA_TEST_SERVICES = "1"
python -m pytest -q tests/integration/test_services_smoke.py
```

### Parsing, pricing and matching

```python
from decimal import Decimal

from src.parsers.gaeb_parser import parse_gaeb, write_x84
from src.services.pricing_service import PricingService
from src.services.matching_service import HybridMatchingService

parsed = parse_gaeb("tender.x83")
pricing = PricingService().price_offer(quote.lines, margin_rate=Decimal("0.15"))
matches = HybridMatchingService().rank(tender_item, catalog_candidates)
```

GAEB validation supports optional XSD schemas (point `xsd_dir` at the official
GAEB 2021-05 schemas) and always runs deterministic penny reconciliation. The
X84 writer requires an approved offer plus an `ApprovalService` that verifies
the Ed25519 signature.

### Live AI, Docling and unit conversion

- `DEEPSEEK_ENABLED=true` plus `DEEPSEEK_API_KEY` activates the real
  OpenAI-compatible DeepSeek client in `build_agent_context()`; there is no mock
  in the production path. The LangGraph workflow calls `triage_tender()` before
  enrichment and uses `extract_item_specs()` for missing item attributes.
  Triaged-irrelevant tenders stop with `triage_rejected`; LLM/network failures
  add a warning and fall back to deterministic processing.
- PDF uploads call `parse_pdf()` without an injected converter, so the real
  Docling `DocumentConverter` is used when the `parsing` extra is installed.
- `UnitConverter` converts mismatched supplier/tender units using auditable
  packaging factors from `SupplierQuoteLine.pack_size` or attributes such as
  `units_per_carton`, `packs_per_carton` and `meters_per_roll`. It rewrites
  prices, tiers, MOQ and quantities before deterministic pricing; missing
  factors raise an explicit conversion error instead of guessing.

```python
from decimal import Decimal

from src.models.enums import UnitOfMeasure
from src.services.pricing_service import PricingService
from src.services.unit_converter import UnitConverter

converted = UnitConverter().convert_line(quote_line, UnitOfMeasure.STUECK)
priced = PricingService().price_line(converted, quantity=Decimal("5000"))
```

### Catalogue seeding

```powershell
# CSV: article_number, description, supplier, material, color, glove_size, unit,
# unit_price_net, pack_size, attributes (JSON)
python -m src.admin seed-catalog --file catalog.csv
python -m src.admin seed-catalog --file catalog.json --embedding-provider local
python -m src.admin seed-catalog --file catalog.csv --dry-run
```

The CLI (`yasmina-admin seed-catalog`) validates each row, maps German material /
colour / size / unit text through the normalizers, generates embeddings and
upserts `catalog_articles` by `(supplier, article_number)`.

- `auto` uses an OpenAI-compatible embeddings endpoint when
  `EMBEDDING_API_KEY` or `DEEPSEEK_API_KEY` is configured, otherwise hashing
  vectors are refused unless `--allow-hash-embeddings` is explicit.
- `--embedding-provider local` uses sentence-transformers / BGE-M3 (install the
  `catalogue` extra).
- `--embedding-provider hashing` is deterministic and intended for offline tests
  and CI only; it is not a semantic model.
- `--dry-run` validates rows without calling an embedding endpoint or writing the
  database.

### AI extraction evals

`tests/evals/golden/extraction_cases.json` holds German tender snippets with
expected material, colour, glove-size and pack-size values. The offline integrity
test always runs; when `DEEPSEEK_API_KEY` is set, the eval calls the live client
and fails if field accuracy drops below the 85 % baseline:

```powershell
$env:DEEPSEEK_API_KEY = "..."
python -m pytest -q tests/evals/test_extraction_eval.py -s
```

### E-invoicing (ZUGFeRD / XRechnung)

```python
from datetime import date
from decimal import Decimal

from src.generators import Invoice, InvoiceLine, InvoiceParty
from src.generators import XRechnungGenerator, ZUGFeRDGenerator

invoice = Invoice(
    invoice_number="INV-2026-0001",
    issue_date=date(2026, 1, 15),
    due_date=date(2026, 2, 14),
    seller=seller,
    buyer=buyer,
    buyer_reference="991-03730-19",  # Leitweg-ID
    lines=[line],
    total_net=Decimal("100.00"),
    total_vat=Decimal("19.00"),
    total_gross=Decimal("119.00"),
)

XRechnungGenerator().generate(invoice, "invoice.xml")
ZUGFeRDGenerator().generate(invoice, "invoice.pdf")
```

- XRechnung 3.0 is emitted as UBL 2.1 with the XRechnung customization ID and
  validated against **both** the official EN16931 rules and the KoSIT
  XRechnung 3.0.2 CIUS Schematron (vendored under
  `src/generators/schematron/kosit/`). Validation runs locally through Saxon-HE;
  fatal/error findings abort generation instead of producing a merely
  XSD-valid document.
- Leitweg-ID validation enforces 5–46 characters, segment structure and the
  MOD-97-10 check number.
- Invoice numbers are allocated by `InvoiceRepository` from a row-locked yearly
  counter in the same transaction as the invoice insert. A failed insert rolls
  the counter back, so `INV-YYYY-0001`, `0002`, … remain gapless. The counter
  refuses to exceed `9999`.
- `invoices` stores the seller/buyer address blocks, line items, multi-rate VAT
  splits (19 %, 7 %, 0 %), `paid_amount`, `credited_amount`, credit-note number
  and status (`ISSUED`, `PARTIALLY_PAID`, `PAID`, `OVERDUE`,
  `PARTIALLY_CREDITED`, `CREDITED`, `CANCELLED`).
- Credit notes are issued by `CreditNoteRepository` from their own gapless
  `GS-YYYY-XXXX` counter. A credit note stores its reason, net/VAT/gross totals
  and per-rate VAT allocation, and is applied to the invoice in the same
  transaction.
- ZUGFeRD/Factur-X embeds valid EN 16931 CII XML in a dynamically rendered
  branded PDF/A-3 carrier. The layout uses BayerClean, Reithmayrstraße 52,
  93055 Regensburg, an address block, line table, VAT split and totals.
  `pdf_template.py` draws with ReportLab and embeds the DejaVu Sans/Bold TrueType
  faces from `src/generators/fonts/`, so no Base-14 font is referenced; pypdf
  then adds `/Lang`, `/MarkInfo`, an sRGB output intent, PDF/A-3 XMP
  identification and trailer file identifiers. Pass `base_pdf=` to supply an
  externally certified PDF/A-3 carrier instead.
- The CII XML is XSD-validated by the `factur-x` package; the UBL invoice is
  checked against UBL 2.1 plus KoSIT Schematron in the test suite.
- `saxonche` is required for production XRechnung validation and is part of the
  `e-invoicing` extra. `XRechnungGenerator.build(validate_schematron=False)` is
  available only for controlled offline diagnostics.

### Agent workflow and HITL approval

```python
from src.agents.graph import build_agent_graph
from src.agents.state import AgentContext, OfferRequest, initial_state
from src.services.approval_service import ApprovalService

context = AgentContext(
    approval=ApprovalService(settings.ed25519_approval_public_key),
    llm=deepseek_client,  # optional; None disables LLM enrichment
)
graph = build_agent_graph(context)
result = graph.invoke(
    initial_state(
        tender=tender,
        supplier_quotes=quotes,
        catalog=candidates,
        request=OfferRequest(customer_name="Stadt Muster", offer_number="AN-2026-1", valid_until=deadline),
    )
)
```

The graph enriches missing specs with DeepSeek, matches the catalogue, prices
deterministically, and stops at `pending_approval` with an unsigned
`CustomerOffer`. A human signs the canonical payload out-of-band; rerun with the
approved offer to pass the cryptographic gate and export X84. The server only
needs `ED25519_APPROVAL_PUBLIC_KEY`; private keys stay with the approver.

```powershell
# approver workstation
yasmina-approval sign --offer offer.json --private-key-file approver.key `
  --approver "C. Mueller" --output signed.json

# server / CI
yasmina-approval verify --offer signed.json --public-key $env:ED25519_APPROVAL_PUBLIC_KEY
```

GitHub Actions (`.github/workflows/ci.yml`) has two gates:

- **quality** — `ruff check .`, `ruff format --check .`, `mypy src` (strict),
  byte compilation, offline migration rendering and the `.env` tracked-file
  guard. The same job renders `scripts/generate_sample_invoice.py` to a PDF and
  runs the [veraPDF](https://verapdf.org/) CLI (`verapdf/cli:latest -f 3b`)
  against it, failing the build unless the report contains
  `isCompliant="true"` for the PDF/A-3b profile.
- **tests** — Python 3.11 and 3.12 jobs with `pgvector/pgvector:pg16` and
  ClamAV services plus a MinIO server container. The workflow applies Alembic,
  creates the MinIO bucket, runs the real-service preflight, then runs the full
  pytest suite with `YASMINA_TEST_DATABASE_URL` so the live PostgreSQL tests
  execute.

CI uses `.[dev,e-invoicing]`; local installations may also add
`.[catalogue]` for local BGE-M3 embeddings.

---

## Configuration

All settings live in `src/config.py` and are documented in `.env.example`.

| Group | Examples |
|---|---|
| Application | `APP_ENV`, `APP_DEBUG`, `APP_SECRET_KEY`, `APP_TIMEZONE` |
| API / JWT | `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_EXPIRY_MINUTES`, `REFRESH_TOKEN_EXPIRY_DAYS`, `API_ADMIN_*`, `PASSWORD_MIN_LENGTH`, `PASSWORD_HASH_ALGORITHM`, `MAX_UPLOAD_SIZE_MB` |
| Rate limiting | `RATE_LIMIT_ENABLED`, `RATE_LIMIT_AUTH_PER_MINUTE`, `RATE_LIMIT_UPLOAD_PER_MINUTE`, `RATE_LIMIT_BUSINESS_PER_MINUTE` |
| Invoice seller | `COMPANY_NAME`, `COMPANY_STREET`, `COMPANY_CITY`, `COMPANY_POSTAL_CODE`, `COMPANY_VAT_ID`, `COMPANY_IBAN`, `COMPANY_EMAIL`, `COMPANY_PHONE` |
| PostgreSQL | `POSTGRES_*`, optional `DATABASE_URL`, `DATABASE_ECHO` |
| MinIO | `MINIO_*` |
| ClamAV | `CLAMAV_HOST`, `CLAMAV_PORT`, `CLAMAV_TIMEOUT_SECONDS` |
| Preflight | `PREFLIGHT_TIMEOUT_SECONDS` |
| DeepSeek | `DEEPSEEK_ENABLED`, `DEEPSEEK_API_KEY`, `DEEPSEEK_CHAT_MODEL`, `DEEPSEEK_REASONER_MODEL` |
| Embeddings | `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_TIMEOUT_SECONDS`, `EMBEDDING_BATCH_SIZE` |
| Embeddings | `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` (BGE-M3, 1024) |
| n8n | `N8N_WEBHOOK_BASE_URL`, `N8N_WEBHOOK_HMAC_SECRET`, `N8N_RFQ_WEBHOOK_PATH`, `N8N_CALLBACK_URL`, `N8N_CALLBACK_MAX_AGE_SECONDS` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_APPROVAL_CHAT_ID`, `TELEGRAM_ALERTS_ENABLED`, `TELEGRAM_TIMEOUT_SECONDS`, `TELEGRAM_MAX_RETRIES`, `TELEGRAM_RETRY_BACKOFF_SECONDS` |
| ERPNext | `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`, `ERPNEXT_COMPANY`, `ERPNEXT_ITEM_GROUP`, `ERPNEXT_SELLING_PRICE_LIST`, `ERPNEXT_QUOTATION_SERIES` |
| Portal scrapers | `SCRAPER_REQUEST_DELAY_SECONDS`, `SCRAPER_ROBOTS_CACHE_SECONDS`, `SCRAPER_MAX_PAGES`, `SCRAPER_ARCHIVE_RAW`, `SCRAPER_ARCHIVE_PREFIX`, `SCRAPER_USER_AGENTS`, `SCRAPER_HARVEST_ATTACHMENTS`, `SCRAPER_MAX_ATTACHMENTS`, `SCRAPER_MAX_ATTACHMENT_MB`, `SCRAPER_SCHEDULE_CRON` |
| HITL | `ED25519_APPROVAL_PUBLIC_KEY`, `APPROVAL_SIGNATURE_REQUIRED` |
| Pricing | `DEFAULT_VAT_RATE`, `DEFAULT_MARGIN_RATE`, `OFFER_VALIDITY_DAYS` |

Secrets use `SecretStr` and are masked in logs. Startup validates the JWT secret
(minimum 32 characters), rejects default application/JWT/API-admin/n8n HMAC
secrets in production, and verifies that Argon2id password hashing is available.

---

## Domain model

- **Tender / TenderItem** — tender metadata and normalized positions (quantity,
  unit, glove size, material, colour, thickness in µm, packaging); portal
  discoveries use `source_format=PORTAL` and retain the raw artifact key.
- **SupplierQuote / SupplierQuoteLine / PriceTier** — supplier pricing with MOQ
  and volume breaks.
- **CustomerOffer / CustomerOfferLine** — derived selling prices, net/VAT/gross
  totals and the Ed25519 approval envelope.
- **CatalogArticle** — supplier catalogue with a pgvector embedding and HNSW
  cosine index for semantic matching.
- **WorkerRun** — start/finish, duration, item/error counts and details for every
  scrape portal run and ingestion batch.
- **Invoice / InvoiceLine / InvoiceVatSplit** — legally issued invoice linked to
  one offer, gapless yearly `INV-YYYY-XXXX` number, line-level VAT rates,
  persisted VAT buckets, payment/credit amounts and artifact keys.
- **CreditNote** — gapless `GS-YYYY-XXXX` correction document with reason,
  totals, per-rate VAT allocation and JSONB line snapshot.
- **InvoiceEvent** — append-only, hash-chained lifecycle log (`ISSUED`,
  `PAYMENT_RECORDED`, `CREDIT_ISSUED`, `STATUS_CHANGED`, `CANCELLED`) with
  sequence, actor, amount, reference and payload.
- **User / RefreshToken / TokenBlocklist** — Argon2id identities, hashed rotating
  refresh sessions and revoked access-token JTIs.
- **AuditLog** — append-only, hash-chained compliance trail.

### Database guarantees

| Concern | Enforcement |
|---|---|
| HITL approval | Pydantic validators + PostgreSQL `CHECK` constraints |
| Offer totals | Pydantic validators + `total_gross = total_net + total_vat` |
| Ed25519 format | 128 lowercase hex characters (64-byte signature) |
| Audit immutability | `BEFORE UPDATE/DELETE/TRUNCATE` triggers raising an exception |
| Offer idempotency | unique `offer_number` + approval signature/payload comparison |
| Workflow durability | PostgreSQL checkpoint header/blob/write tables |
| Portal serialisation | PostgreSQL session-level advisory locks per portal |
| Worker observability | `worker_runs` history + `/healthz/worker` snapshot |
| Invoice numbering | row-locked yearly counter updated in the invoice transaction |
| Credit-note numbering | separate row-locked `GS-YYYY-XXXX` yearly counter |
| Multi-rate VAT | per-line rate + `invoice_vat_splits` rows and JSONB snapshot |
| Billing event log | append-only `invoice_events` + SHA-256 `prev_hash` chain |
| Event immutability | `BEFORE UPDATE/DELETE/TRUNCATE` triggers raising an exception |
| Identity | unique username/email + Argon2id password hashes |
| Refresh sessions | SHA-256 token hashes, rotation families, reuse detection |
| Access revocation | `token_blocklist` JTI rows checked on every authenticated request |
| Vector search | `vector(1024)` + HNSW `vector_cosine_ops` (`m=16`, `ef_construction=64`) |

---

## Security

- Never commit `.env`, API keys, private keys or exported customer documents.
- The Ed25519 **private** key belongs to the human approver, not the server; the
  application only stores the public key for verification.
- Database users are hashed with Argon2id. Raw passwords never enter logs or
  database columns; the password minimum length is 12 by default.
- JWT secrets are validated at startup (minimum 32 characters, no `change-me`
  values in staging/production). Access tokens live 15 minutes by default and
  carry a `jti`; logout adds that JTI to `token_blocklist`.
- Refresh tokens are opaque random values stored only as SHA-256 hashes. Every
  refresh rotates the token within a family; reuse of a rotated token revokes
  the complete family.
- The four-eyes rule blocks the offer/tender owner (or signed `approved_by`
  matching the owner) from approving the offer. Sales users can only access
  tenders and offers they own; admins and viewers retain the documented wider
  read access.
- Rate limits protect `/auth` (brute force), `/tenders/upload` and the wider
  business API. The limiter is in-memory per process; horizontally scaled
  deployments should switch the bucket store to Redis.
- Rotate all `change-me` values before any non-local deployment.
- `audit_logs` entries are chained with SHA-256 over canonical JSON; changing a
  historical row requires rewriting the entire chain.

## Roadmap

- [x] P0.1 — tooling, environment, Docker services (PostgreSQL/pgvector, MinIO,
      ClamAV, n8n), configuration, preflight checks
- [x] P0.2 — strict schemas, ORM, pgvector, Alembic, audit log
- [x] P1 — GAEB/PDF parsers, XSD/penny validation, pricing and hybrid matching
- [x] P1 — FastAPI health/readiness endpoints and startup preflight lifespan
- [x] P1 — LangGraph orchestration and DeepSeek extraction client
- [x] P1 — Ed25519 approval payload, signing, verification and approval gate
- [x] P1 — pgvector catalogue retrieval, JWT/RBAC FastAPI business API and API integration tests
- [x] P1 — PostgreSQL tender repository and ClamAV-scanned uploads
- [x] P1 — MinIO upload/artifact storage, audit-chained scans and invoice download endpoints
- [x] P1.4 — ERPNext quotation sync and HMAC-signed n8n RFQ/quote integration
- [x] P2.1 — ZUGFeRD 2.2/Factur-X and XRechnung 3.0 generators with Leitweg-ID validation
- [x] P2.2 — robots-compliant Bayern/Bund portal scrapers, raw artifacts, CPV filter and ingestion bridge
- [x] P3 — live DeepSeek triage/spec extraction, real Docling PDF path, unit conversion and daily scrape worker
- [x] P4.1 — durable offer persistence, idempotent approvals, owner-aware offer API and PostgreSQL LangGraph checkpoints
- [x] P4.2 — portal attachment download to MinIO, durable ingestion outbox, watermark-based incremental scraping and queued DeepSeek/Docling processing
- [x] P4.3 — PostgreSQL advisory portal locks, SIGINT/SIGTERM graceful shutdown, persisted worker run history, `/healthz/worker` metrics and Telegram failure/dead-letter alerts
- [x] P4.4 — invoice ORM with gapless `INV-YYYY-XXXX` numbering, KoSIT Schematron validation, branded PDF/A-3 carrier invoices and multi-rate VAT records
- [x] P4.5 — Argon2id users, short-lived JWTs, rotating refresh-token families, JTI blocklist, four-eyes approval, ownership-scoped APIs and token-bucket rate limiting
- [x] P4.6 — CI real-service containers (PostgreSQL/pgvector, MinIO, ClamAV), ruff/mypy quality gates, pgvector catalogue seeding CLI and DeepSeek extraction evals
- [x] P4.7 — embedded-font PDF/A-3 with a veraPDF CI gate, `GET /invoices` + payment and credit-note REST endpoints, gapless `GS-YYYY-XXXX` credit notes and a GoBD append-only hash-chained invoice event log
- [x] P2 — Telegram notifier (failure/dead-letter alerts) and production ERPNext/n8n workflow templates

See `docs/DETAILED_SPEC.md` for the engineering contract and
`docs/ASSUMPTIONS.md` for fields inferred while Sections 5–8 are still being
written.

## License

Proprietary — all rights reserved. No license is granted for redistribution or
commercial use without written permission.
