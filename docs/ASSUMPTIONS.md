# P0/P1 Inference Notes — Domain & Service Assumptions

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

## E-invoicing assumptions (P2.1)

- Invoice lines use the shared `UnitOfMeasure`; UBL/CII unit codes are mapped to
  UN/ECE `C62`, `KGM`, `LTR`, `MTR` and `MTK`.
- XRechnung buyer reference (BT-10) is validated as a Leitweg-ID: 5–46
  characters, coarse/fine/check segments and ISO 7064 MOD 97-10 check digits.
  Private B2B references can bypass the Leitweg check explicitly.
- ZUGFeRD/Factur-X CII XML is validated against the Factur-X EN16931 XSD bundled
  with the `factur-x` library. The default PDF is a blank base document; a real
  PDF/A-3 renderer/template should be supplied via `base_pdf` for production.
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
