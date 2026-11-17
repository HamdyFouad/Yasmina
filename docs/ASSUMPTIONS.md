# Inference Notes — Domain & Service Assumptions

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
| `TenderItem` | position/lot, short/long text, quantity & unit, glove size, material, colour, thickness (mm), dimensions, pack data, delivery location/date, CPV, alternative flag |
| `SupplierQuote` | supplier identity, quote reference, tender link, status, validity, payment/Incoterms/delivery days, lines, total, raw extraction |
| `SupplierQuoteLine` | line number, description, tender item link, material/colour/size, quantity/unit, unit price, MOQ, volume tiers, pack, GTIN, delivery days |
| `CustomerOffer` | offer number, customer, tender link, status, lines, margin, net/VAT/gross, validity, terms, Ed25519 approval, ERPNext reference, GAEB export path |

## Service assumptions (P1)

- **Pricing:** volume tier price applies to the whole line quantity; the highest
  qualifying tier wins. MOQ/pack rounding is opt-in (`round_to_moq=True`) because
  the customer-facing quantity usually stays at the tendered amount. Margin is a
  fraction (0.1500 = 15 %), stored with at most four decimal places; unit prices
  round half-up to 4 dp, totals to 2 dp.
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
  A stale `pending` reservation older than
  `N8N_DISPATCH_PENDING_TTL_SECONDS` is reclaimed by the next matching request
  (same operation and request hash); `yasmina-admin reclaim-dispatch-reservations`
  also marks all older rows as `failed` for scheduled operational cleanup.
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
  payment/credit state. Credit notes have their own gapless `GS-` number and
  receive a ZUGFeRD PDF/A-3 plus XRechnung UBL CreditNote, exposed through the
  credit-note document route (implemented in P7.10).
- The billing tests create records under the isolated year 2099, disable the
  append-only triggers only for teardown and re-enable them before returning, so
  a failed test cannot leave the compliance guard off.
- P4.7 brings the default suite to 323 tests and the live PostgreSQL suite to
  fourteen tests.

## Account lifecycle, MFA & auth-audit assumptions (P4.8)

- TOTP follows RFC 6238 with SHA-1, six digits and a 30-second step; the ±1 step
  window, digits and interval are configurable (`MFA_TOTP_*`). The implementation
  is stdlib-only (`hmac`/`hashlib`) and is validated against the RFC 6238 test
  vectors, so no additional runtime dependency is introduced.
- Enrolled secrets are sealed with Fernet using a key derived from
  `APP_SECRET_KEY` (`sha256(app_secret_key)`). Losing or rotating that key makes
  existing enrolments undecryptable; the account then has to re-enrol, which is
  safer than silently accepting an unusable second factor.
- `users.mfa_last_step` stores the highest accepted time step. A code whose step
  is not strictly greater is rejected even if the HMAC still matches, so a
  captured code cannot be replayed inside its validity window. The same counter
  drives `POST /auth/mfa/verify` and the login challenge.
- MFA stays disabled between `POST /auth/mfa/setup` and
  `POST /auth/mfa/verify`, so an interrupted enrolment cannot lock a user out.
  `POST /auth/mfa/disable` requires both the account password and a live code
  and revokes every refresh session afterwards.
- The bootstrap admin (`API_ADMIN_*`) is configuration, not a database row, so it
  cannot enrol MFA, change its password through the API or hold refresh tokens.
  It remains an emergency access path and its password is managed by the
  deployment configuration.
- Password reset tokens are `secrets.token_urlsafe(32)` values stored only as
  SHA-256 hashes, with `expires_at`, `used_at`, `issued_by` and `requested_ip`.
  Issuing a new token invalidates the previous unused token for that account.
  Consumption is atomic (`SELECT … FOR UPDATE`), single-use, and revokes all
  refresh sessions. The new password is validated before the token is burned.
- `POST /auth/password/reset/request` always returns `202` without a token to
  avoid account enumeration. Delivery uses the existing Telegram notifier
  (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_APPROVAL_CHAT_ID`); when that channel is not
  configured the token is issued but not delivered, and an operator uses the
  admin endpoint instead. No SMTP/email integration exists yet.
- `POST /auth/users/{username}/password/reset` is admin-only and returns the raw
  token once for out-of-band handover. This is equivalent to the existing
  privilege of creating users with a chosen password, and it is audited.
- Authentication audit events are written through the existing hash-chained
  `audit_logs` service: `USER_LOGIN_SUCCEEDED`, `USER_LOGIN_FAILED`,
  `USER_LOGOUT`, `USER_LOGGED_OUT_ALL_DEVICES`, `USER_CREATED`,
  `USER_PASSWORD_CHANGED`, `USER_PASSWORD_RESET_REQUESTED`,
  `USER_PASSWORD_RESET_COMPLETED`, `USER_MFA_ENABLED`, `USER_MFA_DISABLED` and
  `SYSTEM_TOKEN_CLEANUP`. Passwords, reset tokens and TOTP secrets are never
  included in payloads. A failed audit append surfaces as a server error rather
  than succeeding the request, so identity events are not silently dropped.
- `yasmina-admin purge-auth-tokens` deletes refresh tokens whose `expires_at` has
  passed and revoked sessions older than `AUTH_TOKEN_RETENTION_DAYS` (7), expired
  `token_blocklist` rows and used/expired password reset tokens. `--dry-run`
  counts the same predicates without deleting. Each real run appends a
  `SYSTEM_TOKEN_CLEANUP` audit entry with the counts.
- P4.8 brings the default suite to 339 tests and the live PostgreSQL suite to
  twenty tests.

## Deployment & observability assumptions (P5.1)

- The production image is a two-stage build on `python:3.12-slim`. The builder
  resolves the selected extras into `/opt/venv`; the runtime stage keeps the
  virtualenv, `/app/src`, Alembic files and `libpq5`/`postgresql-client`/`curl`/
  `tini`. The application runs as UID/GID `10001` (`yasmina`) with `tini` as
  PID 1.
- `INSTALL_EXTRAS` defaults to `ai,e-invoicing,parsing` so the image supports
  DeepSeek, Factur-X/XRechnung, Docling and pyGAEB out of the box. Because
  Docling pulls PyTorch, the default image is large; the CI Docker job builds
  `ai,e-invoicing` to keep the gate fast, and operators can drop `parsing` when
  PDF/GAEB ingestion is not required.
- Bytecode is compiled at build time (`compileall`) for both `src` and
  site-packages; the runtime sets `PYTHONDONTWRITEBYTECODE=1` so the root
  filesystem can be mounted read-only. Uvicorn runs with `--no-access-log`
  because `RequestContextMiddleware` already emits one structured JSON access
  line per request, avoiding duplicates.
- `docker-compose.prod.yml` intentionally keeps secrets in a git-ignored
  `.env.prod` (`env_file:`) rather than Docker secrets: the stack is designed
  for a single-host Compose deployment, and Docker secrets require Swarm or
  external secret drivers. The file also feeds `docker compose --env-file`, so
  interpolation of `API_HOST_PORT` and `INSTALL_EXTRAS` works.
- Only the API binds a host port (`127.0.0.1:8000` by default). PostgreSQL,
  MinIO and ClamAV have no published ports and share the `internal: true`
  `backend` network. Resource limits use `deploy.resources`, which `docker
  compose up` honours for non-Swarm deployments (Compose v2).
- The API container applies migrations before starting uvicorn. That is
  convenient for single-host deployments but means a failed migration keeps the
  API down (fail closed); multi-replica deployments should run
  `alembic upgrade head` once as a separate job instead.
- Logging defaults to JSON (`LOG_FORMAT=json`) with `timestamp`, `level`,
  `logger`, `message` and any structured `extra=` fields. `X-Request-ID` is
  honoured from incoming proxies or generated, echoed on the response and
  included in the access log together with method, matched route template,
  status, duration and client address. `LOG_FORMAT=text` remains available for
  local terminals.
- Metrics use the `prometheus-client` default registry, so the Python process
  and platform collectors are exposed alongside the application metrics.
  HTTP labels use the matched route template to bound cardinality (unmatched
  paths become `unmatched`), and `/metrics` is excluded from its own counters.
  The endpoint is unauthenticated like standard Prometheus scrape targets; it
  must be restricted at the network/proxy layer. `METRICS_ENABLED=false` removes
  both the middleware and the endpoint.
- LangGraph metrics are recorded per workflow HTTP execution with a normalized
  status (`pending_approval`, `approved`, `rejected`, `error`, `other`). They
  cover end-to-end workflow duration, not individual node timings; per-node
  tracing remains the job of the LangGraph checkpointer and application logs.
- Backups use `pg_dump --format=custom` and encrypt the archive with Fernet
  (AES-128-CBC + HMAC-SHA256) derived directly from `BACKUP_ENCRYPTION_KEY`, so
  the key must be a valid Fernet key (`python -m src.admin generate-backup-key`).
  Rotating the key makes old objects undecryptable unless the previous key is
  retained out-of-band; there is deliberately no key hierarchy in this task.
- The backup object key contains the UTC date and a random suffix; retention
  deletes objects older than `BACKUP_RETENTION_DAYS` only after the new upload
  succeeds, and prune failures are logged without failing the run. The dedicated
  `BACKUP_BUCKET` is created by `minio-init` in the prod stack and on demand by
  the storage client (`put_bytes` ensures the bucket exists).
- A successful backup appends `SYSTEM_BACKUP_COMPLETED` to the audit log. If the
  audit insert fails, the backup object is already stored; the CLI logs the
  audit failure and still reports the upload, because the backup artifact is the
  primary outcome.
- `pg_dump`/`pg_restore` must match the PostgreSQL major version. The runtime
  image installs Debian's `postgresql-client` (16 on `python:3.12-slim`), which
  matches the pinned `pgvector/pgvector:pg16` server. Restores require
  decrypting the object first and running `pg_restore` against a
  `postgresql://` DSN.
- P5.1 brings the default suite to 359 tests; the live PostgreSQL suite remains
  at twenty tests, and CI additionally builds the production image and verifies
  the non-root UID, importability and precompiled bytecode.

## Catalogue operations & seeding assumptions (P5.2)

- `catalog_articles.manual_overrides` is a JSONB list of field names an operator
  pinned (for example `["description", "unit_price_net"]`). A seed import writes
  only fields it actually supplies and never overwrites a pinned field unless
  `--ignore-manual-overrides` is passed; that repair mode also removes the pins
  it rewrote, so subsequent imports behave normally again.
- Omitted import fields never clear existing data. `NULL` material/colour/unit,
  a missing pack size and the implicit `NOT_APPLICABLE` glove size are treated as
  "not supplied". Explicitly clearing a value is an administrative edit, not an
  import side effect.
- `attributes` merge key-by-key (existing keys retained, incoming keys win on
  collision) unless `attributes` itself is pinned, in which case the whole map
  is protected. Identity fields `supplier`/`article_number` are immutable in the
  merge: changing them creates a new article, which is why the database unique
  constraint is the final duplicate guard.
- Duplicate rows inside one file collapse to the last occurrence and are
  reported as `duplicates`; this keeps a single `INSERT … ON CONFLICT` target per
  article. Supplier names are matched case-insensitively for duplicate detection
  but stored exactly as supplied for existing suppliers.
- `catalog_articles.last_imported_at` and `embedding_model` record provenance for
  operators; the merge does not use them to decide updates.
- Embedding batches are executed by `src/services/embedding_pipeline.py`, which
  owns retry/backoff and progress reporting. Providers classify HTTP 429 as
  `EmbeddingRateLimitError` (with `Retry-After` in seconds when present) and
  timeouts/transport/5xx errors as `EmbeddingTransientError`; the pipeline
  retries those (`EMBEDDING_MAX_RETRIES`) with exponential backoff capped at 30
  seconds, then fails with the batch index. Non-retryable errors fail immediately.
- Progress is written to stderr: an in-place line on a TTY, structured log
  records otherwise, so `--json` command output on stdout stays machine-readable.
  Hashing embeddings remain refused without `--allow-hash-embeddings`, including
  during reindex; a reindex `--dry-run` only counts candidates and therefore does
  not require provider credentials.
- `reindex-catalog` pages by primary key (`id > last_id`) and updates only
  `embedding`, `embedding_model` and `updated_at`. It never inserts or deletes
  rows, commits per page, and is restartable. With an unchanged product text the
  new vector is deterministic for local/openai providers; upgraded models
  naturally produce different vectors for every row. `--only-missing` restricts
  the pass to rows whose embedding is `NULL`; `--respect-overrides` skips rows
  whose `embedding` field is manually pinned. Each successful run appends
  `SYSTEM_CATALOG_REINDEXED` with processed/skipped/model counts.
- The golden extraction dataset now holds 19 German snippets across paper towels,
  gloves (nitrile and latex) and garbage bags. The offline integrity test asserts
  category coverage (at least four per category), unique ids and enum-valid
  expectations; the live DeepSeek accuracy gate remains at 85 % field accuracy.
- P5.2 brings the default suite to 376 tests and the live PostgreSQL suite to
  twenty-four tests.

## Security hardening, Redis & load-testing assumptions (P5.3)

- Distributed rate limiting keeps the token-bucket semantics but evaluates the
  bucket atomically in Redis with a Lua script (`HGET`/`HSET` on `tokens` and
  `updated_at`, `PEXPIRE` of one window). All replicas therefore share one
  budget per `scope:client`; keys are prefixed `yasmina:ratelimit:`.
- The Redis limiter uses `time.time()` (wall clock) rather than `monotonic()`
  because the timestamp must be comparable across processes/hosts. Small clock
  skew between API replicas can shift refill by milliseconds, which is
  acceptable for rate limiting; NTP-synchronised hosts are assumed.
- Backend selection: `RATE_LIMIT_BACKEND=auto` (default) uses Redis when
  `REDIS_URL` is set and the in-process bucket otherwise. Explicit `redis`
  without a URL fails Settings validation, and the production compose sets
  `RATE_LIMIT_BACKEND=redis` with a password-protected Redis service.
- Redis failures are handled explicitly: `RATE_LIMIT_FAIL_OPEN=true` (default)
  allows traffic and logs a warning when Redis is unreachable; `false` returns
  429/1s. This is a deliberate availability/security trade-off, configurable per
  environment.
- `TRUST_PROXY_HEADERS` defaults to `false`. When enabled, the left-most valid
  address in `X-Forwarded-For` wins, with `X-Real-IP` as fallback; invalid or
  missing values fall back to the socket peer. Operators must only enable it
  when the API is not directly reachable, otherwise clients can spoof their
  identity and evade per-IP budgets. The prod example enables it because the API
  is published on loopback behind a proxy.
- The API builds the Redis client lazily on the first rate-limited request so
  the async connection pool binds to the serving event loop; the application
  lifespan closes it on shutdown.
- CI's `security` job runs Bandit over `src` with a medium/high threshold
  (`-ll`). Low-severity observations (asserts, B105 false positives on audit
  event names, an intentional `try/except/pass` fallback in PDF extraction) stay
  in the report but do not fail the build; raising the gate to `-l` would
  require individual `# nosec` annotations.
- Trivy scans the built image for OS and Python package vulnerabilities and
  fails on fixable CRITICAL/HIGH findings. The runtime image removes `pip`,
  `setuptools` and `wheel` from both the base interpreter and the virtualenv,
  which removes the fixable `setuptools` CVE and reduces the attack surface; no
  `pip install` is possible inside the running container.
- The load test is an operator tool, not part of the default suite. It uses the
  optional `performance` extra (`locust`), authenticates a database user with MFA
  disabled, and requires `YASMINA_LOAD_TENDER_ID` to exercise the pgvector match
  endpoint. Credentials are supplied via environment variables and never
  committed. The scenario asserts nothing automatically; it produces Locust
  HTML/CSV reports plus `/metrics`, `pg_stat_activity` and Redis
  `INFO commandstats` for interpretation.
- P5.3 brings the default suite to 390 tests; the live PostgreSQL suite stays at
  twenty-four tests and CI additionally runs two live Redis rate-limit tests
  (`YASMINA_TEST_REDIS_URL`).

## Operations dashboard assumptions (P5.4–P5.5)

- The dashboard is a separate Vite SPA in `frontend/`, not bundled into the API
  image. In development it uses the Vite `/api` proxy to `127.0.0.1:8000`, so
  the browser stays same-origin. For separate hosting, `VITE_API_BASE_URL`
  points at the backend, `CORS_ALLOWED_ORIGINS` must list the dashboard origin
  and `CORS_ALLOW_CREDENTIALS` is enabled because the refresh token is a cookie.
  Both hosts should share a site (for example `ops.example.com` and
  `api.example.com`) because the cookie is `SameSite=Strict`; serve them over
  HTTPS and set `REFRESH_COOKIE_SECURE=true`.
- React 18 is pinned although the Vite template ships React 19; Tailwind is
  pinned to v3 so the classic `tailwind.config.ts` and shadcn/ui token setup stay
  explicit. shadcn primitives are vendored under `src/components/ui` (Radix
  primitives + `class-variance-authority`) instead of running the interactive
  `shadcn` CLI, keeping the build reproducible in CI.
- Strict TypeScript is enforced (`strict`, `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`). Decimal money fields arrive as strings from
  Pydantic/v1 serializers and are typed as strings; the UI formats them with
  `Intl.NumberFormat`. No `any` is used in application code.
- Access tokens are kept in memory only. The refresh token is an `HttpOnly`,
  `SameSite=Strict`, optionally `Secure` cookie scoped to
  `REFRESH_COOKIE_PATH` (default `/api/v1/auth`); it is never returned in JSON.
  `/auth/refresh` rotates the cookie and `/auth/logout` clears it. A page reload
  restores the session by calling `/auth/refresh` with `withCredentials: true`.
  The request-body `refresh_token` fallback is retained for tests and non-browser
  clients, with the cookie taking precedence when both are present. Cross-site
  cookies are out of scope; document that deployments must be same-site.
- The Axios interceptor performs a single-flight refresh on `401`, retries the
  original request once, and emits a session-expiry event when refresh fails.
  `403` and `429` (with `Retry-After`) surface as Sonner toasts; FastAPI
  validation `detail` arrays are flattened into one message.
- MFA is detected from the `X-Yasmina-MFA-Required` response header on the login
  `401`, so the header is explicitly exposed by CORS. The login form then
  submits `totp_code` with the same credentials.
- The overview reads `GET /ops/summary` (owner-scoped for sales) and
  `GET /ops/activity` (admin/viewer only, newest 25 audit rows by default). Sales
  users therefore see KPI cards and billing totals for their own data, while the
  global audit stream stays hidden. `/ops/summary` returns money as strings
  (`gross_invoiced`, `gross_paid`, `currency`) to preserve decimal precision.
- The tender board uses `GET /tenders` (pagination + status filter, owner-scoped
  for sales) and the detail sheet uses `GET /tenders/{id}` plus
  `GET /tenders/{id}/document` for the original GAEB/PDF stream. Matching runs
  on demand through `POST /tenders/{id}/match`; results include lexical,
  semantic and hybrid scores and are not cached server-side.
- Linked pricing offers are found with `GET /offers?tender_id=...` (new optional
  filter) so the dashboard does not have to page the entire offer list. Totals
  shown in the UI are the server-computed net/VAT/gross values; the client never
  recalculates prices. Freight and pallet surcharges are now modelled by the
  deterministic pricing engine as zero-margin offer lines and are included in
  the VAT split and invoice line items; the UI labels them as surcharges.
- Invoice documents are served directly by
  `GET /invoices/{id}/documents/{zugferd|xrechnung}`; the API resolves
  `invoice.offer_id` to the offer number internally, so the browser does not
  chain offer lookups. PDF/A-3 and XRechnung generation still happens during the
  approval workflow, and a missing artifact returns `404`.
- The "Mark as paid" action is only rendered for admin accounts and posts to
  `POST /invoices/{id}/payments`; the server remains the authority on RBAC and
  overpayment validation.
- React Query retries once for transient errors but never for 401/403/404/429.
  Query keys are namespaced by resource; server state is cached for 30 seconds.
- The frontend CI job runs `npm ci`, `oxlint`, `tsc -b && vite build` and the
  Playwright suite (PostgreSQL/MinIO/ClamAV, migrations, seeded API and Vite). It
  does not deploy a production static host.
- The suite runs with live PostgreSQL, Redis, MinIO and ClamAV enabled in CI.

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

## Resolved questions for spec reconciliation (P7.9)

The six questions left open in earlier phases are now decided and enforced by
Pydantic schemas, database constraints and/or documented service rules:

1. **Source-format naming.** The canonical `TenderSourceFormat` values are
   `GAEB_X83`, `GAEB_D83`, `GAEB_X84`, `PDF_DOCLING`, `PORTAL`, `API` and
   `MANUAL`. They are stored verbatim in `tenders.source_format`, and migration
   `0015` adds `ck_tenders_source_format_valid` so a non-canonical string cannot
   reach the database even outside the API.
2. **Supplier-quote shape.** Both modes are supported and intentional:
   `tender_id IS NULL` marks a free-standing/catalogue quote, while a quote with
   `tender_id` may link each line to a `tender_item_id`. `tender_item_id` stays
   nullable so a tender-bound quote can still include a catalogue substitute; the
   workflow only consumes lines whose `tender_item_id` matches a tender position.
3. **Margin semantics.** The header `margin_rate` is the requested/default
   margin; each offer line carries its own effective `margin_rate` (products use
   the request value, freight/pallet use `0.0000`). The schema therefore does
   not require line margins to equal the header. Margins are decimal fractions
   with at most four decimal places (`0.1500` = 15 %), enforced by
   `MarginRate`/`OfferRequestPayload` and `NUMERIC(6,4)` columns.
4. **Thickness units.** Every `TenderItem` stores `thickness_mm` in
   millimetres, regardless of product family. Parser/LLM inputs accept `mm` or
   `µm` and convert to mm; the field is bounded to `0.001–2.000` mm with three
   decimal places. Migration `0015` renames the old µm column and converts
   existing values by dividing by 1000.
5. **Approval roles and four-eyes.** Only the `approver` or `admin` role may
   call `POST /approvals/approve`; `approved_by` is always the authenticated
   principal (server-side signing), never a client-supplied name. The approver
   must differ from the offer owner/tender creator (case-insensitive), enforced
   in the route and as a `CustomerOffer` schema invariant. `admin` can approve,
   but not their own offer; `sales` and `viewer` cannot approve at all.
6. **Embedding model and dimension.** Production uses `BAAI/bge-m3` at `1024`
   dimensions, matching the pgvector `vector(1024)` column and HNSW index. The
   setting remains overridable for tests/alternative providers, but changing it
   for a production database requires a schema migration; it is not a per-request
   option. The catalogue records the embedding model per article.

## Hash-chained audit log

`audit_logs` is append-only (UPDATE/DELETE/TRUNCATE blocked by triggers) and
chained with SHA-256 over canonical JSON including `occurred_at`, `event_type`,
`actor`, `entity_type`, `entity_id`, `payload` and `prev_hash` (the database
`id` is deliberately excluded so the hash is stable before insert). Appends are
serialized with a PostgreSQL advisory lock inside the same transaction, so the
chain remains linear across concurrent writers. `audit_entry_hash` is exposed
for deterministic fixture and chain-verification tests.

## Portal expansion assumptions (P6.3)

- `config/portals.yaml` is the source of truth for the 120-target catalogue.
  Each target names an engine (`cosinex`, `rib`, `standard`, `xvergabe`, `dtvp`)
  and may add `region`, `portal_code`, `domain`, `request_delay_seconds`,
  `max_pages` and an optional `api` block. Duplicate ids and non-HTTP(S) URLs
  are rejected at load time.
- Only `bayern` and `bund` are enabled by default because they are the two
  portals verified against their live HTML. The other 118 entries are
  discovery targets marked `metadata.verified: false`; enabling one is a
  deliberate operator action after a pilot parse. This avoids a scheduled run
  hammering guessed URLs. `python -m src.worker scrape --include-unverified`
  runs the full catalogue once for controlled verification; the daily scheduler
  still uses only enabled targets.
- Engine classes share the generic HTML parser but tune card selectors, detail
  hints and iframe handling. Portal-specific class identity is derived
  dynamically from the target, so no Python class is required per municipality.
- The API preference reads a configured JSON or XML **GET feed**. TED's v3
  search endpoint is a POST API; when a deployment points `api.url` at a TED
  export/feed, the TED adapter's default mapping applies. A POST client is not
  implemented yet. If the feed fails and `fallback_to_html` is true, the worker
  scrapes the HTML engine and records the API error in the scrape report.
- Cross-portal deduplication hashes `v1|normalized title|normalized client|
  UTC deadline date`. The date (not the exact minute) is deliberate because
  syndicated feeds often round or shift the deadline time. `tenders.dedup_hash`
  has a unique constraint; the migration backfills existing rows and leaves
  historical duplicates with a null hash (the first row is canonical).
  Portal-specific `tender_number` remains the first-line check.
- Per-domain spacing and locks use Redis when `REDIS_URL` is set:
  `yasmina:scrape:domain:*` for request spacing and
  `yasmina:scrape:domain-lock:*` for one in-flight scrape per domain. Without
  Redis, process-local versions preserve the same semantics for local use.
  PostgreSQL advisory locks still prevent the same portal from being scraped
  twice across workers.
- Discovery runs in batches of `SCRAPER_MAX_CONCURRENT_PORTALS` (default 6).
  Network/attachment work is concurrent across domains, while database
  persistence and outbox writes stay sequential on one session. A portal
  failure is isolated and logged; other targets and the ingestion pass continue.
- Attachment harvesting remains enabled by default but scraped files are not
  passed through ClamAV (only `POST /tenders/upload` is scanned). This remained
  a P6.3 follow-up and is closed by P6.4 below.

## Ingestion security and ERP hardening assumptions (P6.4)

- Scraped PDF/GAEB attachments are downloaded into memory, streamed through
  ClamAV INSTREAM, and only then written to the ingestion work directory. A
  scanner/connection error fails closed and leaves the job retryable; a
  positive scan discards the bytes before Docling/pyGAEB, marks the
  `TenderIngestionJob` terminal `failed` with a `SECURITY:` error, and appends
  `SYSTEM_SECURITY_ALERT` to the hash-chained audit log. The audit payload
  contains the filename/object key/signature/job/portal, never file bytes.
- Malware rejections count as hard failures for the run record so operators
  notice them; they are not retried.
- Approval no longer calls ERPNext inline. It writes one unique
  `quotation_sync_jobs` row per offer and returns `erpnext.status = "queued"`
  with the job id. The background worker claims with
  `FOR UPDATE SKIP LOCKED`, retries `ERPNextError` with exponential backoff
  (`QUOTATION_SYNC_BACKOFF_SECONDS`, capped at one hour) and moves the job to
  `failed` after `QUOTATION_SYNC_MAX_ATTEMPTS`. Missing credentials leave the
  queue untouched; jobs that become unconfigured are marked `skipped`.
- The offer's `erpnext_quotation_name` is only updated after a successful
  quotation creation. The worker stages the offer update and
  `ERP_QUOTATION_CREATED` audit event in the same transaction as the job
  completion; a failure rolls back and retries. The deterministic ERPNext
  quotation title keeps retries idempotent.
- Admin catalogue overrides are exposed at
  `GET/POST /api/v1/admin/catalog/overrides`. The POST payload resolves the
  article by `(supplier_name, article_number)`, validates fields against
  `MERGEABLE_FIELDS`, and supports `add`, `replace` and `clear` modes. An
  empty `clear` removes every pin. Responses include the current override list
  and article metadata.

## Frontend admin parity and local edge assumptions (P6.5)

- The dashboard Catalogue Admin page is restricted to the `admin` role in both
  the navigation and the route guard. The backend remains the authority and
  returns `403` for every non-admin request.
- The override dialog posts the exact backend contract
  (`supplier_name`, `article_number`, `fields`, `mode`) and treats an empty
  field list in `clear` mode as "remove every pin". The UI shows the backend
  validation error for unknown fields or missing articles.
- `/ops/outbox-status` is admin/viewer only and returns
  `{quotation_sync: {pending, processing, succeeded, failed, skipped}, total}`.
  Sales users do not receive the widget because ERPNext connectivity is a
  platform-operator concern.
- `Dockerfile.frontend` builds the Vite bundle with Node 22 and copies `dist/`
  into a Caddy 2.10 image. Caddy serves the SPA with history fallback and
  reverse-proxies `/api/*`, `/docs*`, `/openapi.json` and `/redoc*` to the
  Compose network alias `yasmina-api:8000`.
- Local HTTPS uses `localhost` + `tls internal`. The certificate is signed by
  Caddy's local CA and stored in the `caddy_data` volume. Browsers must accept
  the certificate once (or the CA can be imported into the host trust store).
  HSTS is still sent, so use `https://localhost` after accepting it.
- `REFRESH_COOKIE_SECURE=true` remains enabled for the production compose
  profile; the SPA and API are same-origin through Caddy, so the
  `SameSite=Strict` cookie flows normally. `CORS_ALLOWED_ORIGINS=https://localhost`
  is only needed for direct API access.
- Caddy exposes ports `80`/`443`; the API remains bound to
  `127.0.0.1:8000` for local debugging. Caddy runs read-only with a
  `NET_BIND_SERVICE` capability and no-new-privileges. Replacing `localhost`
  with the public hostname and removing `tls internal` is the production
  deployment change.

## Offer & approval workspace assumptions (P7.4)

- Offer reads are owner-scoped only for *pure* sales users. A principal holding
  `approver` (or `admin`/`viewer`) sees every offer, including a dual-role
  `sales` + `approver` user; enforcing owner scoping there would make four-eyes
  approval impossible. The approval endpoint remains the authority on the
  four-eyes rule.
- `GET /offers` accepts an optional exact-match `owner` filter for
  approver/viewer/admin. Pure sales users have the filter ignored and are always
  scoped to their own subject, so a crafted `?owner=` cannot escape ownership.
- `GET /offers/{id}` adds a server-computed `vat_breakdown` (`rate`,
  `taxable_amount`, `tax_amount`) built from the offer lines with the same
  `Decimal`/`q2` rounding as `CustomerOffer.total_vat`. The dashboard never
  re-derives financial values from string fields.
- The `/offers` workspace pages 25 rows with status and owner filters, a
  container-query stacked table and a detail side panel (server VAT breakdown,
  signature state, line items). `?offer=<id>` deep-links the panel and is used by
  the Outbox row link.
- The approval dialog is the project's pinned Radix dialog primitive, which
  supplies the native-`<dialog>` contract (top layer, inert page, focus
  containment and return, Esc/overlay dismissal). It is named by `DialogTitle`
  and described by `DialogDescription`, focuses Cancel as the safe action, and
  never hand-rolls a focus trap or `aria-modal`. A four-eyes rejection is shown
  inline with `role="alert"` while the dialog stays open; success closes the
  dialog, toasts and invalidates the offer list, detail and ops widgets.
- The Approve action is offered for `DRAFT`/`PENDING_APPROVAL` rows to
  approver/admin only. Self-owned rows keep the action enabled but warn that the
  server will reject the signature, so the documented 403 path is reachable and
  displayed rather than hidden by client-side logic.
## Invoice issuance from the approval console (P7.5)

- The approve dialog exposes a collapsed "Generate e-invoice" section. Collapsed
  (the default) keeps the previous behaviour: sign and queue ERPNext without an
  invoice. Enabling it builds an `InvoiceRequestPayload` and appends it to the
  same `POST /approvals/approve` request, so the operator performs signing and
  invoicing as one action even though the backend persists the approval before
  generating artifacts.
- Client validation only mirrors cheap UX constraints (required buyer
  street/city/postal code, two-letter country, 5-46 character buyer reference,
  due date, e-mail shape, field max length). Leitweg-ID MOD-97-10 checks,
  KoSIT/Schematron rules and all tax math stay server-side; the browser
  validation is never a security boundary.
- Validation timing follows the pinned guidance: errors appear after `blur` or a
  submit attempt, clear as soon as the field becomes valid, `aria-invalid` and
  the error entry in `aria-describedby` exist only while the message is visible,
  and a failed submit moves focus to the first invalid field plus a form-level
  `role="alert"` summary. The form uses `noValidate` so the custom messages (not
  browser bubbles) are what users see; `required`, `type="email"`, `maxLength`
  and `autocomplete` remain on the controls for assistive tech and autofill.
- A `422` whose detail contains `e-invoice generation failed` means the approval
  was persisted: the dialog stays open, invalidates offers/invoices/`ops`, explains that
  the offer is approved, and permits resubmission, which the replay path uses to
  retry artifact generation. Any other 422/403 is shown without the "saved"
  claim.
- On success the toast includes the returned artifact object keys and download
  URLs, and `['invoices']` plus `['ops']` are invalidated so the invoice list and
  dashboard widgets refresh.
## Playwright golden-path E2E assumptions (P7.6)

- `scripts/seed_e2e.py` is the only fixture source. It writes users, a published
  tender, a `PENDING_APPROVAL` offer (owner `e2e-sales`), a second DRAFT offer
  and one terminal `FAILED` quotation-sync job, then emits
  `frontend/tests/e2e/.fixtures.json`. It never calls the API, MinIO, the
  outbox worker or the scheduler; it only uses the existing repositories/ORM.
- Every run gets a unique suffix (default UTC timestamp, CI uses
  `run_id-run_attempt`) so usernames, tender numbers, offer numbers and the
  tender `dedup_hash` cannot collide on reruns. The fixture file and Playwright
  artefacts are git-ignored.
- The golden path uses two browser contexts: the seeded `approver` performs the
  four-eyes approval, then the seeded `admin` verifies the invoice, records the
  payment and retries the outbox job. This mirrors the real RBAC boundary
  (approvers cannot read invoices; payments are admin-only) instead of giving
  the approver an admin role.
- Playwright runs chromium only, with one worker and no retries because the test
  consumes seeded state (approval/invoice/retry); a retry would need a fresh
  database. Vite is started by Playwright's `webServer` (reusing a local server
  outside CI) and proxies `/api` to `127.0.0.1:8000`.
- The E2E CI job provisions PostgreSQL and ClamAV services plus a MinIO
  container, applies Alembic, seeds fixtures, generates a throwaway Ed25519 key
  pair (never a committed private key), starts uvicorn behind a `/healthz` wait
  loop and installs Playwright Chromium before running `npm run test:e2e`.
  Traces, screenshots, the HTML report and the API log are uploaded on failure.
- ERPNext is intentionally unconfigured in CI, so the approval toast reports
  "signed (ERPNext is not configured)" and the only outbox job is the seeded
  failed one; the spec asserts the `signed` prefix rather than the ERP status.
- Locators are role/label based (`getByRole`, `getByLabel`) so the suite fails
  on real accessibility regressions instead of CSS churn.

## Security & user administration assumptions (P7.7)

- **Recovery codes** are ten codes generated when TOTP enrolment succeeds (and
  replaced on re-enrolment or disable). Each is 8 random bytes rendered as
  uppercase hex grouped in fours; only `SHA-256` digests are stored in
  `mfa_recovery_codes`, with a unique `(user_id, code_hash)` constraint and an
  index on `(user_id, used_at)`. Consuming a code marks `used_at`, counts
  towards the shared brute-force lockout on failure, and appends
  `USER_MFA_RECOVERY_CODE_USED`; the login response and `/auth/me` expose only
  the remaining count. Recovery codes are never written to the audit payload.
- The enrolment secret is regenerated by every `/auth/mfa/setup` call and any
  previous unused recovery codes are deleted, so an abandoned setup cannot leave
  stale codes valid. `/auth/mfa/disable` also deletes all recovery codes.
- **User administration** is admin-only. `GET /auth/users` returns at most 200
  rows ordered by username (the dashboard has no pagination because the target
  tenant is small); `PATCH .../roles` replaces the role set, and admins cannot
  change their own roles or lock their own account (`400`). Locking sets
  `is_active=false`, clears lockout counters and revokes every refresh session;
  access tokens issued before the lock stay valid until their 15-minute expiry,
  which the UI states explicitly. Unlocking reactivates the account and clears
  `failed_login_attempts`/`locked_until`. All three actions append
  `USER_ROLES_CHANGED`, `USER_LOCKED` or `USER_UNLOCKED`.
- The bootstrap admin (`API_ADMIN_*`) is not a database row, so it never appears
  in the Users table and the Settings page explains that MFA enrolment requires
  a database account.
- The frontend validates username (1–128), e-mail format when present, a
  minimum 12-character initial password and at least one role; the API remains
  the authority (Pydantic `strict` payloads and the configured password policy).
  Destructive lock and MFA-disable confirmations focus **Cancel** first, and
  lock/disable dialogs explain their consequences before the destructive
  button.
- The Playwright admin spec creates a uniquely suffixed account on each run
  (`e2e-ui-<base36 time>`), locks it through the confirmation dialog and asserts
  the Cancel focus. It does not delete the account: the CI database is
  disposable, and no user-delete API is exposed by design (accounts are locked,
  not removed, to preserve audit history).

## Billing completion UI assumptions (P7.8)

- The invoice detail sheet reads `GET /invoices/{id}` (invoice + lifecycle
  `events` + `event_chain_valid`) and `GET /invoices/{id}/credit-notes`; it never
  computes VAT or derives status locally. The outstanding amount is the only
  derived display value (`gross - paid - credited`, floored at zero) and is
  recomputed from the server snapshot on every render.
- **Credit notes** remain admin-only, matching the API. The dialog always sends
  an explicit two-decimal `amount` and a trimmed `reason` (minimum 3 characters,
  maximum 4000); a German comma is accepted in the input and normalised to a
  dot before submission. The API still supports an omitted amount for a full
  credit, but the UI prefills the outstanding balance and requires the operator
  to confirm the value. Partial credits move the invoice to
  `PARTIALLY_CREDITED`; a credit equal to the outstanding balance moves it to
  `CREDITED`.
- `StoredCreditNote.as_dict()` now includes the stored `reason` (additive API
  change) so the detail sheet can list the credit-note history with its
  justification. Credit-note documents are not generated by the repository yet
  (`artifacts` is empty), so the sheet only offers the invoice's ZUGFeRD/XRechnung
  downloads.
- After a credit succeeds, the response's invoice and event list seed the
  React Query cache and the `invoices`/`ops` prefixes are invalidated, so the
  sheet and table update in place without a reload. The credit dialog is
  rendered as a nested Radix dialog; it prevents Radix's initial field focus and
  focuses **Cancel** explicitly, because issuing a credit is a financial
  mutation. Validation errors are inline `role="alert"` messages tied to the
  fields with `aria-describedby`; submit moves focus to the first invalid field.
- The **GoBD timeline** is the invoice's `invoice_events` list, oldest first.
  Each entry shows sequence, event type, actor, timestamp, amount/reference and
  an expandable `prev_hash`/`entry_hash` plus payload; the chain badge comes from
  the server's `event_chain_valid` re-derivation. No separate audit endpoint is
  used, so the timeline and the invoice totals are always one consistent read.
- The invoices table now uses the same `.stacked-table` container-query pattern
  as offers/users: semantic table, caption naming the scrollable `region`,
  `tabindex="0"` for keyboard scrolling, sticky column headers and `data-label`
  cells in the stacked layout. The row header is the invoice number so the
  table remains understandable when the column header row is hidden.
- The Playwright golden path issues a €2.00 partial credit from the detail sheet
  before paying, verifying `Partially Credited`, the `GS-` number and the reason.
  The payment dialog then prefills the reduced outstanding balance, and the final
  `Paid` status proves the credited amount was applied consistently.

## Business logic & schema finalization assumptions (P7.9)

- **Thickness** is canonical millimetres everywhere: schema field
  `TenderItem.thickness_mm`, database `NUMERIC(6,3)`, range `0.001–2.000`, three
  decimal places. GAEB/PDF/LLM extraction accepts `mm` and `µm` and converts
  µm/1000; no density or product-family-specific unit switching is invented.
  Migration `0015` adds/converts `thickness_mm` and drops `thickness_um`.
- **Margin** is a per-line decimal fraction with four decimal places; the offer
  header carries the requested/default margin. Surcharge lines carry margin
  `0.0000`, so line margins may legitimately differ from the header. Migration
  `0015` narrows both margin columns from `NUMERIC(9,6)` to `NUMERIC(6,4)`;
  Pydantic rejects a fifth decimal place before the database sees it.
- **Source formats** use the existing canonical enum values (`GAEB_X83`,
  `GAEB_D83`, `GAEB_X84`, `PDF_DOCLING`, `PORTAL`, `API`, `MANUAL`), and the
  migration adds `ck_tenders_source_format_valid` as a database backstop. The
  six reconciliation questions are answered in the resolved-questions section
  above; supplier quotes remain valid in tender-bound and free-standing modes.
- **Four-eyes approval** is enforced twice: the API requires an `approver` or
  `admin` role and rejects an approver equal to the effective owner (casefold),
  while `CustomerOffer` rejects `approved_by == owner` for imported/constructed
  models. The API no longer auto-assigns an owner equal to the approver when a
  new offer has no owner; `approved_by` always comes from the authenticated
  principal rather than the request body.
- **Freight and pallet pricing** is deterministic: a flat net freight amount per
  offer plus `pallet_unit_net × pallet_count`. `OfferRequest`/`OfferRequestPayload`
  accept optional `freight_flat_net`, `pallet_unit_net` and `pallet_count`
  (defaults zero); `PricingService.calculate_charges` validates non-negative
  Decimals/counts and computes amounts with `q2`. The workflow appends
  zero-margin `FREIGHT` and `PALLET` `CustomerOfferLine`s (units `STUECK` and
  `PALETTE`) so the charges participate in line/VAT reconciliation and flow into
  the invoice lines. A zero count or zero rate produces no line.
- **SMTP reset delivery** uses `aiosmtplib` when `SMTP_HOST` and a sender address
  (or username) are configured; the message goes to `users.email`. `SMTP_STARTTLS`
  and `SMTP_SSL` are mutually exclusive. Delivery failures are logged without the
  token and fall back to the existing Telegram notifier; the audit entry records
  only a boolean `delivered` flag.
- **n8n dispatch TTL reclaim** is two-layered. `IntegrationRepository.reserve`
  atomically reclaims a `pending` row only when the operation and request hash
  match and `updated_at` is older than
  `N8N_DISPATCH_PENDING_TTL_SECONDS` (default 900); this lets the same request
  retry after a crash while still returning 409 for key reuse with a different
  body. `reclaim_stale_pending` (exposed as
  `yasmina-admin reclaim-dispatch-reservations`) marks all older `rfq.dispatch`
  rows `failed` for scheduled maintenance and audits
  `SYSTEM_DISPATCH_RECLAIMED`. A reclaimed key is safe to redeliver because n8n
  receives the same idempotency key and deduplicates it.
- The Playwright seed now includes a €25 freight line and a 2 × €12.50 pallet
  line, so the E2E detail sheet verifies the surcharge line items and invoice
  totals alongside the credit-note/payment flow. The invoice list/detail response
  includes `lines`, making the charges visible without recomputing them client-side.

## Account & billing operations assumptions (P7.10)

- **Password recovery.** `/forgot-password` calls the public reset-request
  endpoint and always shows the same confirmation; it never reveals whether the
  account exists. `/reset-password?token=…` accepts a token from the URL or a
  pasted value, requires at least 12 characters, confirms the new password and
  sends it to `POST /auth/password/reset`. The token is single-use and the reset
  revokes every refresh session. The login page links to both pages.
- **Authenticated password change** is available in Account settings and posts
  to `/auth/password/change`; the backend also revokes every refresh session for
  that account. The current browser keeps its short-lived access token until it
  expires, which is documented in the toast. “Sign out everywhere” posts to
  `/auth/logout/all`, clears the local access token and returns to `/login`.
- **Overdue automation.** Each run of the existing daily worker scheduler first
  calls `InvoiceRepository.mark_overdue_batch`, which locks eligible
  `ISSUED`/`PARTIALLY_PAID` rows with `SKIP LOCKED`, moves them to `OVERDUE` and
  appends a `STATUS_CHANGED` event. `python -m src.worker overdue` performs the
  same operation once for cron/operations. The overdue step is best-effort: a
  failure is logged but does not prevent the scrape/ingestion work.
- **Cancellation.** `POST /invoices/{id}/cancel` (admin) accepts a 3–512
  character reason. It is allowed only for `ISSUED`/`OVERDUE` invoices with no
  payments or credits; paid or credited invoices must be corrected with a credit
  note. The transition appends a `CANCELLED` event containing the reason.
- **Credit-note documents.** Issuing a credit note now also builds a ZUGFeRD
  PDF/A-3 and XRechnung UBL CreditNote (document type `381`, preceding invoice
  reference) with the same seller/buyer snapshot as the invoice. Files are stored
  under `credit-notes/<GS-number>/` and exposed through the credit-note document
  route. If document generation fails after the credit was persisted, the API
  returns 422 with a clear message; the credit itself remains applied.
- **Invoice delivery.** `POST /invoices/{id}/email` fetches the stored ZUGFeRD
  PDF and XRechnung XML, sends them through `SmtpNotifier.send_invoice` and only
  then appends an `EMAIL_SENT` event with recipient and artifact names. A
  missing buyer address returns 422, missing artifacts 404, unconfigured SMTP
  503 and a relay failure 502. The event is part of the same hash chain as
  issuance/payment/credit events (migration `0016`).
- **E2E recovery flow.** CI has no mail relay, so the account-recovery spec
  creates a viewer account through the admin API, exercises the public
  forgot-password page, obtains a token through the admin reset endpoint and
  completes the real `/reset-password` form. This still tests the browser flow
  and the single-use token semantics without a live mailbox.

## Commercial intake & offer-creation assumptions (P8.1)

- **Upload ownership.** `POST /tenders/upload` still assigns the authenticated
  uploader as owner. An admin can reassign the tender afterwards through
  `PATCH /tenders/{id}/owner`; the target must be an active account and the
  change is recorded as `TENDER_OWNER_ASSIGNED`. Sales users cannot reassign
  tenders they do not own.
- **Upload feedback.** The dashboard exposes ClamAV rejections, parse errors,
  unsupported/oversized files and structural parser warnings as separate
  accessible states. A successful upload is not discarded: the review panel
  shows every normalized position and keeps the dialog open for owner
  assignment before the operator opens the tender detail.
- **Manual supplier costs.** A parsed tender has no supplier quotation yet, so
  the offer dialog requires one net supplier cost per position. Those inputs are
  assembled into a single `SupplierQuote` whose lines carry the tender item IDs;
  the workflow therefore exercises the real `price_offer` mapping and unit
  conversion path rather than a client-side shortcut. Before inserting the
  offer, the workflow persists the supplier quote because every offer line
  keeps a foreign key to its `supplier_quote_lines` row.
- **Live preview.** Every valid form change is debounced and sent to
  `POST /tenders/{id}/price`. The preview's totals and per-rate VAT split are the
  API's authoritative values; the browser never computes the saved offer total
  from floating-point arithmetic. Saving posts the same payload to
  `POST /tenders/{id}/workflow`, which persists the draft through the existing
  LangGraph/offer repository path.
- **GAEB `.x84` export.** `POST /offers/{id}/x84` generates the bid from the
  stored GAEB source and the approved offer's line prices, persists
  `gaeb_export_path`, and streams the file. `GET /offers/{id}/x84` only serves a
  previously generated file. Both require an approved offer; a PDF-only tender
  or a draft returns `422`/`409`. The offer and tender detail views only show
  the download action for `APPROVED`/`SENT`/`ACCEPTED` offers, preserving the
  Ed25519 human-approval gate on GAEB export.
- **CI fixture.** `frontend/tests/e2e/fixtures/tender-intake.x83` is a small
  pyGAEB-generated GAEB file with one position. Its `award_no` is
  `E2E-INTAKE`, so the intake spec can assert the created tender/offer without
  depending on database-generated identifiers.
