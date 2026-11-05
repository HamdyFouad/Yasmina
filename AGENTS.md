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
- **Workflow & AI:** LangGraph, `langgraph-checkpoint-postgres`, `langchain-openai` (DeepSeek-Chat / DeepSeek-Reasoner).
- **E-Invoicing:** `factur-x` (ZUGFeRD 2.2), custom XML generator (XRechnung 3.0 / EN 16931).

## 4. Directory Structure Enforcement
Maintain the exact directory layout defined in `docs/DETAILED_SPEC.md` (Section 10):
- `src/config.py` (pydantic-settings)
- `src/db.py` (async SQLAlchemy session factory)
- `src/api/` (`security.py`, `deps.py`, `schemas.py`, `routes/tenders.py`, `routes/approvals.py`, `routes/auth.py`)
- `src/repositories/` (`tender_repository.py`)
- `src/models/` (`enums.py`, `schemas.py`, `orm.py`)
- `src/parsers/` (`normalizers.py`, `pdf_parser.py`, `gaeb_parser.py`, `gaeb_validator.py`)
- `src/services/` (`matching_service.py`, `pricing_service.py`, `approval_service.py`, `unit_converter.py`)
- `src/agents/` (`graph.py`, `nodes.py`, `state.py`, `deepseek.py`)
- `src/integrations/` (`clamav_client.py`, `minio_client.py`, `erpnext_client.py`, `n8n_client.py`; Telegram planned)
- `src/scrapers/` (`base_scraper.py`, `bayern_vergabe.py`, `oeffentlichevergabe.py`, `ingestion.py`)
- `src/scheduler.py` and `src/worker.py` (cron scheduler and scrape CLI)
- `src/generators/` (`invoice_models.py`, `leitweg.py`, `zugferd_generator.py`, `xrechnung_generator.py`)
- `tests/` (unit, integration, evals with German golden fixtures)

## 5. Execution Protocol for Coding Agents
When given an implementation task:
1. **Read `docs/DETAILED_SPEC.md`** relevant to the task section first.
2. Implement code adhering strictly to the typed schemas, naming conventions, and decimal precision rules.
3. Verify that unit tests pass and that strict mode validation does not encounter un-normalized string errors.

## 6. Build, Run & Verification Commands
- Setup: `python -m pip install -e ".[dev,parsing,ai]"`; `.env` is created from `.env.example` on first preflight/API start (copy it manually for other tooling).
- Tests: `python -m pytest -q` (config lives in `pyproject.toml`).
- API: `python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload` (startup runs preflight).
- Lint / types: `ruff check .` and `mypy src`.
- Infrastructure: `docker compose up -d postgres minio clamav` (add `--profile automation` for n8n).
- Preflight: `python -m src.preflight` (or `python -m src.preflight --json`).
- Approval: `yasmina-approval sign|verify` (see `src/services/approval_service.py`).
- Scrape worker: `python -m src.worker scrape|schedule` (daily cron via `SCRAPER_SCHEDULE_CRON`).
- Migrations: `python -m alembic upgrade head`; offline SQL preview: `python -m alembic upgrade head --sql`.
- Spec gaps and inferred schema fields are tracked in `docs/ASSUMPTIONS.md`.