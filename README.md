# Project Yasmina

[![CI](https://github.com/HamdyFouad/Yasmina/actions/workflows/ci.yml/badge.svg)](https://github.com/HamdyFouad/Yasmina/actions/workflows/ci.yml)

**AI-orchestrated B2B sourcing, pricing and tender-bidding engine for the German
cleaning & hygiene supplies sector** — paper towels, nitrile/latex gloves, and
waste bags across all thicknesses, volumes and colour standards.

> **Status:** P0 foundation complete (configuration, strict domain schemas, ORM,
> PostgreSQL/pgvector migrations, hash-chained audit log). Parsers, pricing /
> matching services, LangGraph agents and e-invoicing generators are the next
> milestones.

---

## What it does

Yasmina ingests public and private tenders (GAEB `.x83`/`.d83`/`.x84`, PDF tables),
normalizes messy German source text such as `"Nitril"`, `"Gr. 8"`, `"19 %"` or
`"Karton à 1000 Stk"`, matches positions against supplier catalogues, prices
offers deterministically, and prepares ERPNext quotations / GAEB bids and
ZUGFeRD/XRechnung invoices.

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
  preflight.py           # startup checks (config, DB, MinIO, ClamAV, n8n)
  models/
    enums.py             # canonical StrEnums
    schemas.py           # strict Pydantic v2 domain models
    orm.py               # SQLAlchemy 2.0 mappings + pgvector
  parsers/
    normalizers.py       # German text → enums / Decimal (implemented)
    pdf_parser.py        # planned
    gaeb_parser.py       # planned
  services/              # planned: matching, pricing, unit conversion
  agents/                # planned: LangGraph state machine
  integrations/          # planned: ERPNext, n8n, Telegram clients
  generators/            # planned: ZUGFeRD, XRechnung
alembic/                 # async migrations (0001 schema, 0002 audit guard)
docker/                  # PostgreSQL bootstrap (pgvector, n8n database)
docs/
  DETAILED_SPEC.md       # authoritative engineering specification
  ASSUMPTIONS.md         # inferred fields / open spec questions
tests/                   # unit tests (no database required)
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
python -m pip install -e ".[dev]"
```

### 2. Configure

```powershell
Copy-Item .env.example .env
```

Replace every `change-me` placeholder. `.env` is git-ignored and must never be
committed.

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
HNSW index), MinIO health, the ClamAV daemon and n8n reachability. It exits
non-zero on a failed required check; `n8n` only warns unless `--require-n8n` is
passed. The installed console script is `yasmina-preflight`.

### 6. Run tests

```powershell
python -m pytest -q
```

The current 134 tests cover normalizers, settings, strict schemas, ORM metadata,
pgvector/HNSW configuration, preflight checks and offline migration rendering.

GitHub Actions (`.github/workflows/ci.yml`) runs the same suite on Python 3.11
and 3.12, compiles the sources, renders the initial Alembic migration SQL and
checks that `.env` is never tracked.

---

## Configuration

All settings live in `src/config.py` and are documented in `.env.example`.

| Group | Examples |
|---|---|
| Application | `APP_ENV`, `APP_DEBUG`, `APP_SECRET_KEY`, `APP_TIMEZONE` |
| PostgreSQL | `POSTGRES_*`, optional `DATABASE_URL`, `DATABASE_ECHO` |
| MinIO | `MINIO_*` |
| ClamAV | `CLAMAV_HOST`, `CLAMAV_PORT`, `CLAMAV_TIMEOUT_SECONDS` |
| Preflight | `PREFLIGHT_TIMEOUT_SECONDS` |
| DeepSeek | `DEEPSEEK_API_KEY`, `DEEPSEEK_CHAT_MODEL`, `DEEPSEEK_REASONER_MODEL` |
| Embeddings | `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` (BGE-M3, 1024) |
| n8n | `N8N_ENCRYPTION_KEY`, `N8N_WEBHOOK_BASE_URL`, `N8N_WEBHOOK_HMAC_SECRET` |
| ERPNext | `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` |
| HITL | `ED25519_APPROVAL_PUBLIC_KEY`, `APPROVAL_SIGNATURE_REQUIRED` |
| Pricing | `DEFAULT_VAT_RATE`, `DEFAULT_MARGIN_RATE`, `OFFER_VALIDITY_DAYS` |

Secrets use `SecretStr` and are masked in logs; production refuses to start with
the default application secret.

---

## Domain model

- **Tender / TenderItem** — tender metadata and normalized positions (quantity,
  unit, glove size, material, colour, thickness in µm, packaging).
- **SupplierQuote / SupplierQuoteLine / PriceTier** — supplier pricing with MOQ
  and volume breaks.
- **CustomerOffer / CustomerOfferLine** — derived selling prices, net/VAT/gross
  totals and the Ed25519 approval envelope.
- **CatalogArticle** — supplier catalogue with a pgvector embedding and HNSW
  cosine index for semantic matching.
- **AuditLog** — append-only, hash-chained compliance trail.

### Database guarantees

| Concern | Enforcement |
|---|---|
| HITL approval | Pydantic validators + PostgreSQL `CHECK` constraints |
| Offer totals | Pydantic validators + `total_gross = total_net + total_vat` |
| Ed25519 format | 128 lowercase hex characters (64-byte signature) |
| Audit immutability | `BEFORE UPDATE/DELETE/TRUNCATE` triggers raising an exception |
| Vector search | `vector(1024)` + HNSW `vector_cosine_ops` (`m=16`, `ef_construction=64`) |

---

## Security

- Never commit `.env`, API keys, private keys or exported customer documents.
- The Ed25519 **private** key belongs to the human approver, not the server; the
  application only stores the public key for verification.
- Rotate all `change-me` values before any non-local deployment.
- `audit_logs` entries are chained with SHA-256 over canonical JSON; changing a
  historical row requires rewriting the entire chain.

## Roadmap

- [x] P0.1 — tooling, environment, Docker services (PostgreSQL/pgvector, MinIO,
      ClamAV, n8n), configuration, preflight checks
- [x] P0.2 — strict schemas, ORM, pgvector, Alembic, audit log
- [ ] P1 — GAEB/PDF parsers, unit conversion, pricing & matching services
- [ ] P1 — FastAPI endpoints, LangGraph orchestration, HITL approval service
- [ ] P2 — ERPNext/n8n integrations, ZUGFeRD/XRechnung, GAEB `.x84` export

See `docs/DETAILED_SPEC.md` for the engineering contract and
`docs/ASSUMPTIONS.md` for fields inferred while Sections 5–8 are still being
written.

## License

Proprietary — all rights reserved. No license is granted for redistribution or
commercial use without written permission.
