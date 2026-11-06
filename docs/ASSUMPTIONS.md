# P0–P4 Inference Notes — Domain & Service Assumptions

Sections 5–8 of `docs/DETAILED_SPEC.md` (field-by-field schemas and pricing
rules) are not yet published. This file records the contract inferred for the
P0 foundation and the P1 parsers/pricing/matching services so it can be
reconciled with the authoritative spec later.

## Conventions

- All domain models: `ConfigDict(strict=True, extra="forbid", validate_assignment=True, validate_default=True)`.
- `Decimal` is the only accepted numeric type (no `int`/`float`/`str` coercion).
- Money: 4 decimal places for unit prices, 2 for totals, `ROUND_HALF_UP`.
- Immutable timestamps are timezone-aware UTC (`AwareDatetime`).
- Enums are stored in PostgreSQL as `VARCHAR` values; membership is enforced by
  Pydantic, not by native PostgreSQL enum types.
- IDs are UUIDv4 generated application-side with `gen_random_uuid()` fallback.

## Inferred field groups

| Entity | Assumed fields |
|---|---|
| `Tender` | tender number, title, client, procedure, source format, CPV codes, publication/deadline, currency, status, VAT rate, items, metadata |
| `TenderItem` | position/lot, short/long text, quantity & unit, glove size, material, colour, thickness (µm), dimensions, pack data, delivery location/date, CPV, alternative flag |
| `SupplierQuote` | supplier identity, quote reference, tender link, status, validity, payment/Incoterms/delivery days, lines, total, raw extraction |
| `SupplierQuoteLine` | line number, description, tender item link, material/colour/size, quantity/unit, unit price, MOQ, volume tiers, pack, GTIN, delivery days |
| `CustomerOffer` | offer number, customer, tender link, status, lines, margin, net/VAT/gross, validity, terms, Ed25519 approval, ERPNext reference, GAEB export path |

## Service assumptions (P1)

- **Pricing:** volume tier price applies to the whole line quantity; the highest
  qualifying tier wins. MOQ/pack rounding is opt-in (`round_to_moq=True`) because
  the customer-facing quantity usually stays at the tendered amount. Margin is a
  fraction (0.15 = 15 %), unit prices round half-up to 4 dp, totals to 2 dp.
- **Matching:** stage 1 rejects only when both sides expose a conflicting
  material/colour/glove size/unit — missing catalogue attributes are not
  penalized. `PgVectorCatalogRepository` filters in SQL and orders by pgvector
  cosine distance; stage 2 uses 40 % lexical / 60 % embedding cosine by default,
  with a small exact-attribute bonus. A query embedding must come from an
  embedding provider; the API currently accepts one embedding per match request.
- **GAEB parsing:** full OZ is used as `position_number`; missing quantities,
  unknown units and duplicate positions are reported as warnings. Missing dates
  fall back to `now + 30 days`; missing clients use a placeholder.
- **GAEB writing:** only approved offers with an Ed25519 signature and payload
  hash may produce `.x84`. Position prices are matched by full OZ, falling back
  to the leaf OZ.
- **GAEB validation:** penny reconciliation compares item and document totals
  with a one-cent threshold. XSD validation requires an explicit `xsd_dir`; the
  official schemas are not bundled with the project.
- **PDF parsing:** Docling table rows are mapped through German header aliases;
  rows without a position, description or positive quantity are skipped with a
  warning.

## Agent and approval assumptions (P1.2/P1.3)

- The LangGraph workflow is single-pass and sync: enrich → match → price →
  approval gate → X84 export. A resumed pass can carry a signed offer and skip
  re-pricing. Statuses are `pending_approval`, `approved`, `approval_invalid`,
  `exported` and `failed`.
- DeepSeek is used only to fill missing material/colour/size/thickness/pack
  hints. Its output is normalized before merge; LLM failures are warnings, not
  hard failures.
- The approval payload is canonical JSON (sorted keys, compact separators,
  UTF-8) covering the offer, its lines, totals, approver and approval timestamp.
  The Ed25519 signature signs those bytes; the stored SHA-256 hash is a separate
  integrity value. Private keys never belong on the application server.
- The X84 writer requires a configured `ApprovalService` and verifies the offer
  signature cryptographically before writing; the orchestration layer supplies
  the public-key verifier.

## API assumptions (P1)

- JWT uses HS256 with a configurable secret/issuer and roles `admin`, `sales`,
  `approver`, `viewer`. Only the single admin credential can obtain a token from
  `/auth/token`; sales/approver tokens must be minted by an external identity
  layer for now.
- Uploaded tenders and positions are persisted in PostgreSQL via
  `TenderRepository`; the original bytes are stored in the MinIO bucket
  (`MINIO_BUCKET`) under `tenders/{upload_id}/{filename}` and the object key plus
  source filename are retained in tender metadata so a GAEB document can be
  rehydrated for later X84 export. Uploads are therefore portable across API
  workers as long as they share the same object store.
- Uploads are limited by `MAX_UPLOAD_SIZE_MB` and pass a ClamAV INSTREAM scan
  before parsing. Scanning fails closed: if clamd is unavailable the upload is
  rejected with 503. Every completed scan outcome (`clean` or `infected`,
  including signature/engine data) is appended to `audit_logs` before parsing,
  so rejected uploads remain auditable.
- `POST /tenders/{id}/workflow` returns `pending_approval` for an unsigned offer
  and `approved` once a correctly signed offer is supplied. `export_path` is
  restricted to `GAEB_EXPORT_DIR`.
- `POST /approvals/{offer_number}/approve` verifies the signature via
  `ApprovalService`; when the payload carries an `invoice` block it renders
  XRechnung UBL and ZUGFeRD/Factur-X CII bytes, stores them in MinIO under
  `offers/{offer_number}/`, and returns their object keys and download URLs.
  `GET /approvals/{offer_number}/documents/{format}` streams the artefacts back.
  Approval and invoice generation are currently in-memory operations (no offer
  row is written yet); generation or storage failures return 422.

## ERPNext / n8n assumptions (P1.4)

- ERPNext uses Frappe's `/api/resource` REST API with token authentication.
  Items are upserted by `item_code`, Suppliers by `supplier_name`; UOM names map
  to the standard site fixtures (`Nos`, `Box`, `Carton`, `Pallet`, `Roll`, `Kg`,
  `Litre`, `Meter`, `Square Meter`). A deployment that renamed or deleted those
  UOM records must seed them first.
- ERPNext 15 has no customer-reference field on `Quotation`. The deterministic
  `title` (`Yasmina <offer-number> | <customer>`) is therefore the quotation
  idempotency key. A quotation is never created unless the offer carries a
  valid Ed25519 approval (verified when the public key is configured).
- `Item` and `Supplier` sync is read-before-write and safe to retry. HTTP
  429/5xx and transport failures are retried; 4xx validation errors are not.
- n8n outbound and inbound requests use the same HMAC scheme:
  `sha256=HMAC_SHA256(secret, "<unix-timestamp>." + raw_body)`. The secret is
  shared by both directions. Callbacks outside
  `N8N_CALLBACK_MAX_AGE_SECONDS` are rejected.
- Idempotency keys are stored in `integration_idempotency` with the request
  SHA-256 and cached response. A replay with the same key and body returns the
  cached response; a different body or operation is rejected with 409.
- A dispatch reservation is committed as `pending` before the webhook call.
  A failed dispatch is marked `failed` and may be retried with the same key.
  A process crash between reservation and completion leaves a `pending` row
  that currently requires operational cleanup (no automatic TTL reclaim).
- Inbound callbacks expect already-normalized canonical `SupplierQuote` JSON;
  `n8n` is responsible for German-text normalization before signing the body.
  Quote ingestion and the idempotency success marker share one transaction.

## Portal scraper assumptions (P2.2)

- Portal discoveries use the added `TenderSourceFormat.PORTAL` value and
  `TenderStatus.PUBLISHED`; the detail URL is stored in `source_reference`.
- CPV filtering is exact on the eight-digit CPV base. A missing check digit is
  accepted, but codes outside `33760000`, `19520000`, `33141420` and `39830000`
  are never persisted, even if they share a prefix.
- The generic HTML parser accepts article/search-result/list rows. Complete
  listing cards need no detail request; cards without a CPV, deadline or client
  are enriched from the detail page before filtering. Metadata that cannot be
  verified is skipped rather than guessed.
- `vergabe.bayern.de` embeds its publication list in an iframe; the scraper
  follows that iframe only when the landing page contains no cards. The federal
  UI is a JavaScript application, so production deployments may point
  `start_url` at a rendered HTML search export while using the same parser.
- `robots.txt` is cached per origin/User-Agent. HTTP 401/403/429/5xx fail closed;
  ordinary 4xx responses (including the portals' current 404 robots.txt) mean
  no restrictions. Request spacing is the larger of the configured delay and
  robots `Crawl-delay`.
- Raw response bytes are archived to MinIO as
  `scrapers/<portal>/<date>/<sha256>.<html|json|bin>` before parsing. Archival
  failure aborts the request instead of silently parsing unarchived data.
- `PortalIngestionBridge` saves metadata through `TenderRepository` and is
  idempotent by `tender_number`; an existing row is not re-ingested. A pipeline
  hook must be injected to hand the committed tender to the processing graph or
  a queue. Portal notifications currently contain no positions or attachments,
  so the pipeline is responsible for fetching and parsing those documents. A
  hand-off failure after the repository commit therefore leaves a persisted row
  for operational review instead of deleting it.

## Live AI, conversions & scheduling assumptions (P3)

- The production API context has no mock LLM. `DEEPSEEK_ENABLED=true` with a
  non-empty `DEEPSEEK_API_KEY` constructs the real OpenAI-compatible client;
  otherwise enrichment is skipped deterministically. Triage is called at the
  start of the LangGraph workflow, before spec extraction. Triage/spec API
  failures are warnings, not fatal exceptions; a negative triage stops the
  workflow with `triage_rejected`.
- PDF uploads call `parse_pdf()` without an injected converter, so production
  uses Docling's real `DocumentConverter` when the `parsing` extra is installed.
  Test doubles remain confined to dependency-injected tests.
- Unit conversion uses explicit packaging facts only. `SupplierQuoteLine.pack_size`
  is interpreted as pieces per source `PACK`/`KARTON`; additional factors can be
  supplied in `attributes` (`units_per_pack`, `packs_per_carton`,
  `units_per_carton`, `cartons_per_palette`, `units_per_palette`,
  `meters_per_roll`). Weight, volume and area units are only converted to the
  same unit; no density or sheet-count assumptions are invented. Missing
  factors raise `UnitConversionError`.
- The scheduler is a dependency-free five-field cron implementation in
  `APP_TIMEZONE`. The default `0 2 * * *` runs `ingest_scraper()` daily through
  `src.worker`. Job failures are logged without terminating the loop. The
  worker exposes an immediate `scrape` command and a long-running `schedule`
  command; production still injects the pipeline hook when a real ingestion
  queue/graph should be triggered.

## Offer persistence & workflow memory assumptions (P4.1)

- `customer_offers` now stores `owner` and `invoice_artifacts` (MinIO object
  metadata). `CustomerOffer` exposes the same fields; the Ed25519 signature
  deliberately excludes them, so artifact/ERP updates do not invalidate an
  approval.
- Approval is idempotent by `offer_number`. The first request persists the
  approved state before invoice/ERP side effects. A replay with the same
  signature and payload hash returns the stored offer and artifacts; a
  different signature/hash returns `409`. A failed invoice/ERP call leaves the
  approved row in place so the client can retry the missing side effect.
- The approval payload accepts an optional `owner` (sales subject). If omitted,
  the signed offer's `owner` is used, then the authenticated approver. Sales
  users can only read offers where `owner == JWT subject`; viewers and admins
  can read all. Approvers are not granted the read routes by default.
- LangGraph uses `SqlAlchemyCheckpointSaver`, a local equivalent of
  `AsyncPostgresSaver`, with checkpoint header/blob/write tables. Project
  Pydantic models, enums and workflow dataclasses are explicitly registered
  with the serializer. The API thread key is
  `tender:<tender_id>:<offer_number>`.
- The two live PostgreSQL tests are skipped unless
  `YASMINA_TEST_DATABASE_URL` is set; CI without a provisioned database still
  runs the mocked offer/checkpoint behaviour tests.

## Portal attachment & ingestion-outbox assumptions (P4.2)

- Attachment harvesting is on by default (`SCRAPER_HARVEST_ATTACHMENTS`) because
  most portals only expose GAEB/PDF files on the detail page. Only links whose
  path/extension looks like PDF, GAEB/XML or ZIP are fetched; at most
  `SCRAPER_MAX_ATTACHMENTS` (10) files of `SCRAPER_MAX_ATTACHMENT_MB` (25) MiB
  are archived per tender. Downloads reuse the robots/rate-limit/User-Agent
  guardrails and are stored under
  `scrapers/<portal>/attachments/<sha256>.<ext>`.
- Attachment metadata (`url`, `filename`, `object_name`, `content_type`, `kind`)
  is copied into tender metadata and into `tender_ingestion_jobs.attachments`,
  so the worker can parse without fetching the portal again.
- The tender row and its one `TenderIngestionJob` are committed in a single
  transaction. `tender_id` is unique, making re-discovery idempotent; when an
  outbox repository is supplied the ingestion bridge does not run the pipeline
  inline.
- Job statuses are `pending`, `processing`, `completed`, `skipped` and `failed`.
  Claims use `FOR UPDATE SKIP LOCKED`; a `processing` lease older than 900
  seconds is treated as a crashed worker and reclaimed. Transient parse/AI
  failures retry with exponential backoff up to `max_attempts` (5) before the
  row becomes `failed`. Missing tenders, unsupported attachments, no extracted
  positions and non-relevant DeepSeek triage results finish as `skipped`.
- `ScraperState.last_scraped_at` records the start of a run for observability;
  the incremental watermark is `last_success_at`, passed as `since` on the next
  run. `last_error` is retained on failure without moving the watermark.
- The four live PostgreSQL outbox tests are skipped unless
  `YASMINA_TEST_DATABASE_URL` is set; the mocked repository/processor tests cover
  enqueue, claim, retry and completion semantics.

## Worker hardening & observability assumptions (P4.3)

- Portal serialisation uses PostgreSQL session-level advisory locks rather than
  a lock table. The key is a deterministic signed 64-bit BLAKE2b digest of
  `yasmina:portal-lock:<lowercase portal>`. Each scrape checks out a dedicated
  connection, calls `pg_try_advisory_lock`, and keeps that connection open until
  the scrape finishes. A competing worker receives `False`, records a `skipped`
  run and continues with the next portal. A crashed process releases the lock
  automatically when PostgreSQL closes the connection.
- SIGINT and SIGTERM only set a cooperative `asyncio.Event`. The current portal
  scrape and the current DeepSeek/Docling job are allowed to finish; the worker
  then releases locks, returns claimed-but-unstarted outbox rows to `pending`
  (decrementing the speculative attempt), and exits. Scheduler idle waits wake
  immediately instead of sleeping until the next cron minute. A second signal
  is treated the same way; there is no hard-cancel path.
- `worker_runs` stores one row per portal scrape and one row per ingestion
  batch, with `run_type` (`scrape`/`ingestion`), resource, status
  (`running`/`succeeded`/`failed`/`skipped`/`cancelled`), start/finish,
  `duration_ms`, `items_processed`, `errors_count`, an error summary and a JSONB
  detail payload. `items_processed` counts successfully parsed/ingested jobs;
  retryable failures increment `errors_count` without incrementing the item
  count. `GET /healthz/worker` reports `degraded` (HTTP 200) for a failed latest
  run or a non-empty dead-letter queue and 503 only if metrics cannot be read.
- Telegram alerting is optional and enabled only when both
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_APPROVAL_CHAT_ID` are set. A scheduled
  scrape exception sends one alert after the run is recorded; an ingestion job
  that reaches the terminal `failed` state sends one dead-letter alert. Delivery
  is best-effort, retries transient HTTP failures, and never changes the job or
  run status.
- Worker locking, run history and Telegram tests add two live PostgreSQL tests
  alongside the existing outbox/offer checks; the identity task adds three more,
  for a live PostgreSQL suite of twelve tests, all skipped unless
  `YASMINA_TEST_DATABASE_URL` is set.

## Production invoice & billing assumptions (P4.4)

- A `CustomerOffer` has at most one `invoices` row (`offer_id` unique). The
  invoice is linked by `offer_id`; the artifact object keys remain under the
  offer prefix for backward-compatible download URLs.
- Invoice numbers are allocated from `invoice_number_sequences` using a row lock
  (`SELECT … FOR UPDATE`) in the same transaction as the invoice/line inserts.
  An invoice rollback also rolls back the counter, so `INV-YYYY-0001`,
  `INV-YYYY-0002`, … contain no gaps. The yearly counter is capped at `9999`
  and numbering is scoped to the invoice issue date's year.
- `CustomerOfferLine` now carries its own `vat_rate`; `CustomerOffer.total_vat`
  is validated against the line-rate split rather than a single global rate.
  Migration 0007 backfills existing lines from `customer_offers.vat_rate`, so
  legacy single-rate approvals continue to validate and verify.
  `invoices.vat_breakdown` stores an exact string snapshot and
  `invoice_vat_splits` stores one row per rate. Line rates and split rates use
  `Numeric(6, 4)`; monetary values remain `Decimal` with two places.
- Payment and credit state is derived only through repository methods
  (`record_payment`, `record_credit`, `mark_overdue`) with bounded amounts.
  Full payment sets `PAID`; partial payment `PARTIALLY_PAID`; full credit
  `CREDITED`; partial credit `PARTIALLY_CREDITED`. Credit notes never reuse an
  invoice number and are referenced by `credit_note_number`.
- KoSIT validation executes the vendored official XSLT rule sets for both
  EN16931 and XRechnung 3.0.2 CIUS, using Saxon-HE (`saxonche`) locally. Only
  `fatal`/`error` findings block generation; warnings and informational findings
  remain visible in `KoSITValidationReport`. The release and attribution are in
  `src/generators/schematron/kosit/NOTICE`.
- The branded carrier PDF is rendered by `pdf_template.build_branded_pdf` with
  BayerClean, Reithmayrstraße 52, 93055 Regensburg defaults, German address /
  line / VAT / totals blocks, PDF/A-3 XMP identification and an sRGB output
  intent. Factur-X embedding preserves the XMP metadata and output intent in
  the generated hybrid PDF. A caller may still pass `base_pdf=` for an
  externally certified PDF/A-3 carrier.
- KoSIT requires seller contact name, telephone and e-mail plus buyer/seller
  electronic endpoints; `COMPANY_EMAIL`, `COMPANY_PHONE`, the buyer e-mail and a
  payment account (IBAN) must therefore be configured in production.
- Invoice integration tests create records under the isolated year 2099 and
  clean them up, so they never consume production invoice numbers.

## Identity, RBAC & security assumptions (P4.5)

- `users.roles` is a JSONB list of canonical role values (`admin`, `sales`,
  `approver`, `viewer`). Passwords are hashed with Argon2id (time cost 3,
  64 MiB, parallelism 4) and must contain at least 12 characters by default.
  Raw passwords are never stored or logged. Five failed logins lock the account
  for 15 minutes; the counter resets on success or lock.
- Access tokens are short-lived (15 minutes by default), carry `sub`, `roles`,
  `jti`, `iat`, `exp`, `iss` and `type=access`. Every authenticated request
  checks the `token_blocklist` for the token's `jti`; logout inserts the JTI
  with its expiry so the token cannot be replayed even before `exp`.
- Refresh tokens are opaque 48-byte URL-safe random values. Only their SHA-256
  hex digest is stored. Each refresh rotates the token in the same
  `family_id`; the prior token is marked revoked and linked to its replacement.
  Presenting an already-rotated token is treated as reuse and revokes the whole
  family. Logout can revoke the supplied refresh token. The configured admin
  bootstrap login remains available for local/emergency access and receives an
  access token only; database users receive both tokens.
- The ownership rule is: a sales user may only read/use tenders and offers they
  own. `tenders.owner` is set from the authenticated uploader; `offers.owner` is
  set from the authenticated workflow creator. Admins bypass ownership checks;
  viewers retain documented read access. An existing persisted/tender owner
  takes precedence over client-supplied `owner` fields for the four-eyes check.
- Four-eyes: an approval is rejected when the authenticated subject equals the
  effective owner, or when the signed `approved_by` equals the owner. This is
  enforced in the approval endpoint and in the workflow persistence path; it
  applies even to a user holding both `sales` and `approver` roles.
- Rate limiting uses an in-process token bucket keyed by client IP and scope:
  `/api/v1/auth` (20/min), `/api/v1/tenders/upload` (30/min) and other
  `/api/v1` routes (300/min). A denied request returns `429` with `Retry-After`
  and rate-limit headers. This is intentionally dependency-free; horizontal
  deployments should move the bucket store to Redis for a shared budget.
- Startup calls `validate_security_configuration` before serving: the JWT secret
  must have at least 32 characters, staging/production reject placeholder
  secrets and short admin passwords, Argon2 must be importable, and only
  `argon2id` is accepted. The same check is part of `python -m src.preflight`.
- The three live PostgreSQL identity tests cover user auth, refresh
  rotation/reuse and JTI blocklisting. At the P4.5 milestone this brought the
  live suite to twelve and the default suite to 315; P4.6 adds catalogue and
  service coverage (320 defaults, thirteen live DB tests).

## CI, catalogue seeding & eval assumptions (P4.6)

- The quality job runs `ruff check .`, `ruff format --check .` and `mypy src`
  under the strict configuration in `pyproject.toml`. FastAPI's intentional
  `Depends`/`File`/`Query` calls in argument defaults are exempted from B008;
  external packages without type metadata (lxml, facturx, saxonche, docling,
  asyncpg, sentence-transformers, reportlab, langchain-openai) have explicit
  mypy import overrides rather than broad `Any` suppressions. langchain-openai is
  only imported lazily inside `DeepSeekClient`, so it stays an optional `ai`
  extra while the type checker tolerates its absence.
- The test job uses `pgvector/pgvector:pg16` and `clamav/clamav:stable` services
  plus a MinIO server container. Upstream MinIO stopped serving anonymous Docker
  Hub pulls, so both CI and `docker-compose.yml` use the pinned `pgsty/minio`
  mirror of the official release binaries. The job applies Alembic, creates the
  MinIO bucket, runs `python -m src.preflight --skip n8n` against the real
  services and then the full pytest suite with `YASMINA_TEST_DATABASE_URL`, so
  the live DB tests are mandatory in CI.
- Catalogue files accept CSV/TSV/JSON with at least `article_number` and
  `description`; `supplier` may come from a column or `--supplier`. German
  material/colour/size/unit/package strings pass through the same normalizers as
  tenders before persistence. Upserts key on `(supplier_id, article_number)`.
- `auto` embeddings use an OpenAI-compatible `/embeddings` endpoint when
  `EMBEDDING_API_KEY` or `DEEPSEEK_API_KEY` is configured; otherwise the CLI
  refuses hashing vectors unless `--allow-hash-embeddings` is explicit. `local`
  uses sentence-transformers BGE-M3 via the `catalogue` extra. Hashing vectors
  are deterministic test fixtures, never a semantic production model.
- The embedding text concatenates description, normalized attributes, pack size
  and extra catalogue fields. Vectors must match `EMBEDDING_DIMENSIONS` (1024 by
  default) before any upsert.
- The DeepSeek eval loads four German golden snippets. It always checks dataset
  integrity offline and, when `DEEPSEEK_API_KEY` is present, calls the live
  client and fails below the 85 % field-accuracy baseline. Without a key the
  live accuracy test is skipped, matching the CI secret policy.
- P4.6 adds catalogue unit/live tests, the eval and MinIO/ClamAV smoke tests,
  bringing the default suite to 320 tests and the live PostgreSQL suite to
  thirteen tests; the two service smoke tests run in CI with
  `YASMINA_TEST_SERVICES=1` and the live eval remains key-gated.

## Billing completion & PDF/A-3 conformance assumptions (P4.7)

- `pdf_template.build_branded_pdf` draws the carrier with ReportLab and embeds
  the DejaVu Sans/Bold TrueType faces vendored in `src/generators/fonts/`
  (Bitstream Vera / public-domain license, see `LICENSE_DEJAVU`). Base-14 fonts
  are never referenced, so the document renders identically on every host and
  the PDF/A-3 validator finds a `/FontFile2` for every font resource.
- pypdf post-processing adds `/Lang`, `/MarkInfo`, an sRGB output intent, PDF/A-3
  XMP (`pdfaid:part=3`, `pdfaid:conformance=B`) and trailer file identifiers,
  which veraPDF requires. The CI quality job renders
  `scripts/generate_sample_invoice.py` and runs `verapdf/cli:latest -f 3b`; the
  build fails unless the report's `isCompliant` attribute is `"true"`. This is a
  hard conformance gate, not a warning.
- Invoice lifecycle events live in `invoice_events` and are appended by the
  repositories, never by callers: `ISSUED`, `PAYMENT_RECORDED`, `CREDIT_ISSUED`,
  `STATUS_CHANGED` and `CANCELLED`. Each row carries `(invoice_id, sequence)` as
  a unique key, an optional `amount`, `reference` and JSONB `payload`.
- The event chain hashes canonical JSON of the event fields plus `prev_hash`
  with SHA-256, starting from 64 zeros. `InvoiceEventRepository.verify_chain`
  re-derives every hash, and `GET /invoices/{id}` exposes the resulting
  `event_chain_valid` flag. PostgreSQL triggers block `UPDATE`, `DELETE` and
  `TRUNCATE`, so the log is append-only in the GoBD sense; deletion for tests
  must disable `trg_invoice_events_append_only` explicitly.
- Credit notes have their own gapless `GS-YYYY-XXXX` sequence
  (`credit_note_number_sequences`, yearly, capped at `9999`). They are separate
  from the `INV-` invoice series so a credit note never consumes an invoice
  number, and they reuse the invoice's VAT buckets: the credited amount is
  allocated across rates, and the resulting per-rate corrections are stored on
  the credit note.
- The billing API is intentionally narrow: `GET /invoices` and invoice detail /
  events / credit-note reads are owner-scoped for `sales` and global for
  `viewer`/`admin`; `POST /invoices/{id}/payments` and
  `POST /invoices/{id}/credits` are admin-only because they mutate legal
  payment/credit state. Credit notes currently produce a persisted document
  record and number; a dedicated ZUGFeRD credit-note PDF is out of scope for
  P4.7.
- The billing tests create records under the isolated year 2099, disable the
  append-only triggers only for teardown and re-enable them before returning, so
  a failed test cannot leave the compliance guard off.
- P4.7 brings the default suite to 323 tests and the live PostgreSQL suite to
  fourteen tests.

## E-invoicing assumptions (P2.1)

- Invoice lines use the shared `UnitOfMeasure`; UBL/CII unit codes are mapped to
  UN/ECE `C62`, `KGM`, `LTR`, `MTR` and `MTK`.
- XRechnung buyer reference (BT-10) is validated as a Leitweg-ID: 5–46
  characters, coarse/fine/check segments and ISO 7064 MOD 97-10 check digits.
  Private B2B references can bypass the Leitweg check explicitly.
- ZUGFeRD/Factur-X CII XML is validated against the Factur-X EN16931 XSD bundled
  with the `factur-x` library. The default carrier is now the branded PDF/A-3
  template from `src/generators/pdf_template.py`; `base_pdf` remains available
  for an externally certified carrier (see P4.4).
- VAT is grouped per rate; the invoice model enforces line, net, VAT and gross
  reconciliation.

## Open questions for spec reconciliation

1. Exact naming of the GAEB/PDF "source format" enum values.
2. Whether supplier quotes are stored per tender item only, or also as free
   catalogue quotes (this schema supports both).
3. Official margin semantics (header-level margin vs. per-line margin).
4. Whether thickness must be stored in µm for bags but mm for gloves.
5. Required approval roles / four-eyes rules for `approved_by`.
6. Embedding dimension and model (currently BGE-M3 at `1024`, configurable).

## Hash-chained audit log

`audit_logs` is append-only (UPDATE/DELETE/TRUNCATE blocked by triggers) and
chained with SHA-256 over canonical JSON including `occurred_at`, `event_type`,
`actor`, `entity_type`, `entity_id`, `payload` and `prev_hash` (the database
`id` is deliberately excluded so the hash is stable before insert). Appends are
serialized with a PostgreSQL advisory lock inside the same transaction, so the
chain remains linear across concurrent writers. `audit_entry_hash` is exposed
for deterministic fixture and chain-verification tests.
