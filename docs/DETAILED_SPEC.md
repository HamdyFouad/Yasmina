# Project Yasmina — Sections 4-8: Domain, Workflow, Operations & Deployment Specification
*(File Target: `docs/DETAILED_SPEC.md` — Referenced by root `AGENTS.md`; Sections 5-8 are seeded skeletons pending field-by-field expansion)*

---

## 4.0 Architectural Separation & Pre-Validation Normalization Layer

### 1. Document Separation (`AGENTS.md` vs. `docs/DETAILED_SPEC.md`)
To optimize context-window efficiency for **OpenCode / DeepSeek Harness**:
- **`AGENTS.md` (Root Directory):** Contains concise operational rules, build/test commands, directory pointers, and strict coding guardrails (< 150 lines).
- **`docs/DETAILED_SPEC.md` (This Document):** Serves as the authoritative engineering reference containing full schemas, enums, SQL filter rules, and cryptographic specifications.

### 2. Mandatory Pre-Validation Normalization Layer (`src/parsers/normalizers.py`)
Because all domain schemas enforce `ConfigDict(strict=True, extra="forbid")`, passing raw German strings extracted by `Docling`, `pygaeb`, or an LLM (e.g., `"Nitril"`, `"Gr. 8"`, `"19%"`, `"transparent/klar"`, `"Karton à 1000 Stk"`) directly into Pydantic constructors will immediately raise a `ValidationError`.

**Strict Architectural Rule:** Parsers (`pdf_parser.py`, `gaeb_parser.py`) and LLM structured-output nodes **must never instantiate domain models directly from raw text**. All raw extraction dictionaries must pass through `src/parsers/normalizers.py` first:

```python
# Conceptual flow in src/parsers/normalizers.py
from decimal import Decimal
from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure

GLOVE_SIZE_EN420_MAP: dict[str, GloveSize] = {
    "5": GloveSize.XS, "5.5": GloveSize.XS, "6": GloveSize.XS, "6.0": GloveSize.XS, "xs": GloveSize.XS,
    "6.5": GloveSize.S, "7": GloveSize.S, "7.0": GloveSize.S, "s": GloveSize.S, "klein": GloveSize.S,
    "7.5": GloveSize.M, "8": GloveSize.M, "8.0": GloveSize.M, "m": GloveSize.M, "mittel": GloveSize.M,
    "8.5": GloveSize.L, "9": GloveSize.L, "9.0": GloveSize.L, "l": GloveSize.L, "groß": GloveSize.L, "gross": GloveSize.L,
    "9.5": GloveSize.XL, "10": GloveSize.XL, "10.0": GloveSize.XL, "xl": GloveSize.XL,
    "10.5": GloveSize.XXL, "11": GloveSize.XXL, "11.0": GloveSize.XXL, "xxl": GloveSize.XXL,
}

GERMAN_COLOR_MAP: dict[str, StandardizedColor] = {
    "blau": StandardizedColor.BLUE,
    "schwarz": StandardizedColor.BLACK,
    "gelb": StandardizedColor.YELLOW,
    "rot": StandardizedColor.RED,
    "grün": StandardizedColor.GREEN, "gruen": StandardizedColor.GREEN,
    "grau": StandardizedColor.GREY, "anthrazit": StandardizedColor.GREY,
    "braun": StandardizedColor.BROWN,
    "orange": StandardizedColor.ORANGE,
    "rosa": StandardizedColor.PINK, "pink": StandardizedColor.PINK,
    "violett": StandardizedColor.PURPLE, "lila": StandardizedColor.PURPLE,
    "weiß": StandardizedColor.WHITE, "weiss": StandardizedColor.WHITE, "hochweiß": StandardizedColor.WHITE,
    "transparent": StandardizedColor.TRANSPARENT, "transluzent": StandardizedColor.TRANSPARENT,
    "klar": StandardizedColor.CLEAR, "glasklar": StandardizedColor.CLEAR,
    "natur": StandardizedColor.NATURAL,
    "bedruckt": StandardizedColor.PRINTED_MULTICOLOR, "mehrfarbig": StandardizedColor.PRINTED_MULTICOLOR,
}

def normalize_glove_size(raw: str | int | float | None) -> GloveSize:
    if raw is None:
        return GloveSize.NOT_APPLICABLE
    cleaned = str(raw).lower().replace("größe", "").replace("gr.", "").replace(",", ".").strip()
    return GLOVE_SIZE_EN420_MAP.get(cleaned, GloveSize.NOT_APPLICABLE)
```

### 3. Implementation Notes (Authoritative)
- The production implementation lives in `src/parsers/normalizers.py` and extends the conceptual excerpt above with:
  - umlaut/ß folding (`grün` / `gruen`, `weiß` / `weiss`, `groß` / `gross`);
  - compound colour handling (`"transparent/klar"`, `"blau/weiß"` → first resolvable token);
  - `GERMAN_MATERIAL_MAP` (`"Nitril"` → `MaterialType.NITRILE`) and `GERMAN_UNIT_MAP` (`"Karton à 1000 Stk"` → `UnitOfMeasure.KARTON`);
  - deterministic `decimal.Decimal` parsing for German number formats (`"1.234,56"`), percentages (`"19%"` → `0.19`) and pack contents (`"à 1000 Stk"` → `1000`).
- Normalizers are **pure functions**: no I/O, no network, no LLM calls. Unmappable values raise `NormalizationError` (a `ValueError`) instead of silently producing defaults, except where the domain explicitly defines a fallback (`GloveSize.NOT_APPLICABLE`, `StandardizedColor.UNKNOWN`, `MaterialType.NOT_APPLICABLE`, `UnitOfMeasure.NOT_APPLICABLE`).
- Every raw extraction dictionary is passed through `normalize_raw_extraction()` before Pydantic validation; parsers and LLM nodes never instantiate domain models from raw text.

---

## Section 5 — Commercial Workflow & Lifecycle (skeleton)

**Status:** seeded skeleton. The authoritative implementation is the Pydantic
schemas, repositories and route handlers named below; this section will be
expanded field-by-field once the source specification is published.

**Scope:** tender intake → normalized positions → supplier quotes/RFQ →
deterministic pricing → four-eyes approval → GAEB `.x84` export → ERPNext
quotation outbox → invoice/credit-note lifecycle.

**Invariants to preserve while expanding:**

- `Tender`/`TenderItem` and `CustomerOffer` are strict Pydantic models; no raw
  German extraction enters them before the normalizer layer.
- Pricing is deterministic `Decimal` code in `src/services/pricing_service.py`;
  margin, freight, pallet surcharge, MOQ rounding and VAT never depend on an LLM.
- Approval, `.x84` export and ERPNext quotation creation require a valid Ed25519
  signature and enforce four-eyes ownership rules.
- Every lifecycle transition that affects billing is appended to the GoBD
  invoice event chain; outbox work is durable and retryable.
- Supplier and customer master data are separate from transactional snapshots:
  deactivation hides a record from new RFQs/invoices but never rewrites historical
  quotes, offers or invoices.
- Manual worker commands are durable rows with queued/running/succeeded/failed/
  cancelled states; cancellation is recorded before the process signal.

**Authoritative code:** `src/models/schemas.py`, `src/repositories/tender_repository.py`,
`src/repositories/offer_repository.py`, `src/services/pricing_service.py`,
`src/api/routes/tenders.py`, `src/api/routes/approvals.py`,
`src/api/routes/offers.py`, `src/api/routes/invoices.py`,
`src/repositories/quotation_sync_repository.py`.

---

## Section 6 — API, Identity & Operations Contracts (skeleton)

**Status:** seeded skeleton.

**Scope:** `/api/v1` route contracts, RBAC/ownership rules, four-eyes approval,
ingestion/quarantine operations, worker-run history, manual worker commands and
the public health surface.

**Current contract highlights:**

- Business routes require a bearer access token; refresh tokens are HttpOnly,
  rotating, same-site cookies with a PostgreSQL JTI blocklist.
- Sales users are owner-scoped for tenders/offers/invoices; approvers can read
  offers; admin-only surfaces include users, catalogue overrides and the
  Workers & Ingestion console.
- `/ops/worker-runs` and `/healthz/worker_runs` are admin-only and return the
  same paginated run payload. `/healthz/worker` remains the public snapshot.
- `/ops/ingestion` accepts `status`, `queue` and `quarantined` filters and returns
  filtered `count` plus global status/quarantine counts.
- Quarantine is the dedicated `quarantined`/`quarantine_reason` state; retry and
  discard are admin-only audited actions. Manual scrape/ingestion triggers spawn
  detached workers and return a PID; stale locks can be released with a bounded
  age threshold.
- Errors use FastAPI `detail` payloads; the SPA normalizes them through
  `src/lib/api-error.ts` and announces them with role=`alert`.
- `/suppliers` and `/customers` expose paginated/searchable master data to
  authenticated roles; writes (including soft deactivation and contact
  reconciliation) are admin-only and audit every change.
- Manual worker commands are created before process launch, guarded by a partial
  unique index per active command type, and cancellable through
  `/ops/worker-commands/{id}/cancel`.
- Supplier quotes are accepted explicitly (`POST /supplier-quotes/{id}/accept`);
  only accepted quote lines can generate a draft purchase order, and a quote can
  own at most one PO.
- Purchase orders follow the guarded lifecycle `DRAFT → SENT → CONFIRMED →
  DELIVERED`, with `CANCELLED` allowed from the first three states. Delivery
  fills line `received_quantity` and stamps `delivered_at`.
- Supplier invoices are matched deterministically against PO quantities
  (`0.0001`), unit prices (`0.0001`), line and header totals (`0.01`) and the
  expected delivery date. Deviations persist as stable exception codes and feed
  the Procurement exception queue.
- Margin reporting compares the approved customer offer's quoted cost/margin
  with non-rejected supplier invoices booked against its POs; actual margin is
  unavailable until the first invoice exists.

**Authoritative code:** `src/api/security.py`, `src/api/deps.py`,
`src/api/schemas.py`, `src/api/routes/*.py`, `src/repositories/procurement_repository.py`,
`src/services/procurement_service.py`, `frontend/src/lib/api-client.ts`,
`frontend/src/lib/ops-api.ts`, `frontend/src/lib/procurement-api.ts`.

---

## Section 7 — Data, Compliance & Retention (skeleton)

**Status:** seeded skeleton.

**Scope:** PostgreSQL schema, migration discipline, audit/GoBD guarantees,
quarantine state, retention jobs and object/artifact ownership.

**Invariants:**

- Schema changes ship as Alembic revisions; the CI quality gate renders offline
  SQL and integration jobs run `alembic upgrade head` against PostgreSQL.
- `audit_logs` is append-only and SHA-256 hash-chained. PostgreSQL triggers block
  `UPDATE`, `DELETE` and `TRUNCATE`; secrets/tokens/recovery codes are never
  written to payloads.
- `invoice_events` is a separate GoBD hash chain with the same append-only
  protections; invoice/credit numbering is gapless.
- `tender_ingestion_jobs.quarantined` implies terminal `status = 'failed'` via a
  check constraint. `quarantine_reason` preserves the scanner reason; discard
  keeps the historical reason while clearing the active flag.
- `worker_runs` is retained for `WORKER_RUN_RETENTION_DAYS` (default 90) and
  pruned by `python -m src.admin purge-worker-runs` with an audit entry.
- MinIO objects are referenced by key; SQL deletion does not silently delete
  evidence, and backups remain encrypted under `BACKUP_ENCRYPTION_KEY`.
- Supplier contacts are cascade-owned by suppliers and limited to one primary row
  per supplier; customer/supplier names remain unique. Soft deactivation is the
  default lifecycle operation, preserving historical references.

**Authoritative code:** `src/models/orm.py`, `alembic/versions/*`,
`src/services/audit_service.py`, `src/repositories/invoice_event_repository.py`,
`src/repositories/ingestion_repository.py`, `src/admin.py`.

---

## Section 8 — Deployment, Observability & Verification (skeleton)

**Status:** seeded skeleton.

**Scope:** container images, compose/Caddy edges, CI gates, staging rollout,
metrics/logging, E2E and live-service tests.

**Current contract highlights:**

- Production API image is multi-stage, non-root UID 10001 and removes package
  managers; workers share the API image with different commands.
- CI has seven jobs: quality, Python 3.11 tests, Python 3.12 tests, Docker,
  frontend, security and Playwright E2E. Tests use real PostgreSQL/pgvector,
  MinIO, ClamAV and Redis services.
- Playwright runs against uvicorn + Vite + a signed mock n8n relay; manual worker
  triggers are mocked so the browser suite never spawns a worker.
- Staging deploys SHA-tagged GHCR images, runs `alembic upgrade head`, smoke-tests
  `/healthz` + authenticated `/auth/me` and restores the previous tag on smoke
  failure. Missing `STAGING_*` secrets intentionally skip the rollout.
- `LOG_FORMAT=json`, `/metrics` and worker run history provide operational
  visibility; probes and manual triggers are audited.
- Live-service tests are opt-in with `YASMINA_TEST_DATABASE_URL`,
  `YASMINA_TEST_REDIS_URL` and `YASMINA_TEST_SERVICES`; the CI jobs set them.

**Authoritative code:** `.github/workflows/ci.yml`,
`.github/workflows/deploy-staging.yml`, `docker-compose*.yml`, `Caddyfile*`,
`Dockerfile*`, `scripts/deploy_staging.sh`, `scripts/smoke_test.sh`,
`frontend/playwright.config.ts`, `tests/integration/`.

---
