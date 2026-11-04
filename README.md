# Project Yasmina

[![CI](https://github.com/HamdyFouad/Yasmina/actions/workflows/ci.yml/badge.svg)](https://github.com/HamdyFouad/Yasmina/actions/workflows/ci.yml)

**AI-orchestrated B2B sourcing, pricing and tender-bidding engine for the German
cleaning & hygiene supplies sector** — paper towels, nitrile/latex gloves, and
waste bags across all thicknesses, volumes and colour standards.

> **Status:** P0 foundation and the P1 core are complete: GAEB/PDF ingestion,
> XSD/penny validation, deterministic pricing, pgvector hybrid matching,
> LangGraph orchestration, DeepSeek extraction, Ed25519 approval and a
> JWT-secured FastAPI business API. ERPNext/n8n integrations and e-invoicing
> generators remain.

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
  db.py                  # async SQLAlchemy engine/session factory
  main.py                # FastAPI app + startup preflight lifespan
  preflight.py           # startup checks (config, DB, MinIO, ClamAV, n8n)
  api/
    security.py          # JWT issuance/validation + RBAC roles
    deps.py              # auth, DB session and tender-store dependencies
    schemas.py           # API request DTOs and payload parsers
    store.py             # process-local tender store
    routes/
      auth.py            # POST /auth/token
      tenders.py         # upload, match, price, workflow
      approvals.py       # verify and approve offers
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
    matching_service.py  # pgvector retrieval + lexical/embedding ranking
    approval_service.py  # canonical payload, Ed25519 sign/verify/apply
  agents/
    graph.py             # LangGraph workflow and routing
    nodes.py             # enrich, match, price, approval gate, export
    state.py             # typed state, dependencies and offer request
    deepseek.py          # DeepSeek structured extraction client
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
- `GET /readyz` — startup preflight results
- `GET /docs` — OpenAPI UI

### 7. Business API

All business routes live under `/api/v1` and require a JWT bearer token.

| Method | Path | Role |
|---|---|---|
| POST | `/auth/token` | admin credentials |
| POST | `/tenders/upload` | sales/admin |
| POST | `/tenders/{id}/match` | sales/admin |
| POST | `/tenders/{id}/price` | sales/admin |
| POST | `/tenders/{id}/workflow` | sales/admin |
| POST | `/approvals/verify` | viewer/approver/admin |
| POST | `/approvals/approve` | approver/admin |

```powershell
$token = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/auth/token `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"change-me-admin-password"}').access_token

$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/tenders/upload `
  -Headers $headers -Form @{ file = Get-Item .\tender.x83 }
```

The matching route retrieves candidates directly from `catalog_articles` using
pgvector cosine distance. The workflow route runs the LangGraph state machine;
it returns `pending_approval` until a signed offer is supplied, then
`approved`. Tenders are held in a process-local store in this revision.

### 8. Run tests

```powershell
python -m pytest -q
```

The current 190 tests cover normalizers, settings, strict schemas, ORM metadata,
pgvector/HNSW configuration, GAEB parsing/validation, PDF table extraction,
pricing, hybrid matching, LangGraph orchestration, Ed25519 approval, FastAPI
business routes, RBAC and offline migration rendering.

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

GitHub Actions (`.github/workflows/ci.yml`) runs the same suite on Python 3.11
and 3.12, compiles the sources, renders the initial Alembic migration SQL and
checks that `.env` is never tracked.

---

## Configuration

All settings live in `src/config.py` and are documented in `.env.example`.

| Group | Examples |
|---|---|
| Application | `APP_ENV`, `APP_DEBUG`, `APP_SECRET_KEY`, `APP_TIMEZONE` |
| API / JWT | `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_EXPIRY_MINUTES`, `API_ADMIN_*`, `MAX_UPLOAD_SIZE_MB` |
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
the default application, JWT or API-admin secrets.

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
- [x] P1 — GAEB/PDF parsers, XSD/penny validation, pricing and hybrid matching
- [x] P1 — FastAPI health/readiness endpoints and startup preflight lifespan
- [x] P1 — LangGraph orchestration and DeepSeek extraction client
- [x] P1 — Ed25519 approval payload, signing, verification and approval gate
- [x] P1 — pgvector catalogue retrieval, JWT/RBAC FastAPI business API and API integration tests
- [ ] P1 — unit conversion, durable API persistence, refresh tokens and advanced RBAC
- [ ] P2 — ERPNext/n8n integrations, ZUGFeRD/XRechnung generators

See `docs/DETAILED_SPEC.md` for the engineering contract and
`docs/ASSUMPTIONS.md` for fields inferred while Sections 5–8 are still being
written.

## License

Proprietary — all rights reserved. No license is granted for redistribution or
commercial use without written permission.
