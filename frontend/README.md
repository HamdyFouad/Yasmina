# Yasmina Day 2 Operations Dashboard

Operations console for the Yasmina API: authentication, an overview with KPIs and
the audit activity feed, tender intake with upload/review and server-priced offer
creation, and invoice billing with compliant document downloads.

## Stack

- React 18 + TypeScript (strict, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`)
- Vite 8 with an `/api` dev proxy to `http://127.0.0.1:8000`
- Tailwind CSS 3 with shadcn/ui-style primitives (Radix UI)
- TanStack React Query v5, React Router v6, Axios, Sonner toasts

## Run it

Backend must be running on `127.0.0.1:8000` (see the root README).

```powershell
cd frontend
Copy-Item .env.example .env.local   # optional; defaults to /api/v1 through the Vite proxy
npm install
npm run dev
```

Open <http://localhost:5173> and sign in with an operator account from the
backend configuration (bootstrap admin or a database user). If the account has
MFA enabled, the page reveals the code field after the password step; a
one-time recovery code can be used instead of the authenticator code.

Scripts:

```powershell
npm run dev        # Vite dev server (port 5173)
npm run lint       # oxlint
npm run typecheck  # tsc -b
npm run build      # type-check + production bundle
npm run preview    # serve the built bundle
npm run test:e2e   # Playwright golden path (needs a seeded API)
```

## End-to-end tests

`tests/e2e/golden-path.spec.ts` covers the operator lifecycle in a real browser:
approver login → pending offer → four-eyes approval with an e-invoice →
admin login → invoice detail → partial credit note → payment → outbox retry.
`tests/e2e/account-recovery.spec.ts` exercises the public forgot-password page,
an admin-issued reset token, the `/reset-password` form and a login with the new
password. `tests/e2e/admin-users.spec.ts` logs in as the seeded admin, creates
an account in the Users console, locks it through the cancel-focused
confirmation, and checks that an approver is redirected away from
`/admin/users`. `tests/e2e/tender-intake.spec.ts` uploads the checked-in `.x83` fixture,
reviews parser warnings and positions, reassigns the owner, prices the positions
with freight/pallet surcharges, verifies the server-preview totals and creates
the draft offer. `tests/e2e/tender-to-export.spec.ts` runs the full commercial
loop across a sales context and an approver context: manual supplier quote,
persisted-quote offer pricing, four-eyes approval and the downloaded `.x84`.
`tests/e2e/upload-errors.spec.ts` covers malformed GAEB parsing and a mocked
ClamAV rejection. `tests/e2e/rfq-pending.spec.ts` holds the signed mock relay,
asserts the `409` pending guidance, then recovers after the 2-second dispatch
TTL. `tests/e2e/operations.spec.ts` opens the admin-only Workers & Ingestion
console, checks the seeded worker runs and quarantine/dead-letter rows, filters
runs server-side, opens the run-detail drawer, renders a mocked read-only
portal-probe report with HTML position counts, exercises retry/discard with
cancel-first focus, and triggers the mocked scrape/ingestion/release-lock
actions. `tests/e2e/master-data.spec.ts` creates/deactivates a supplier and
customer and verifies saved-supplier RFQ prefill; `tests/e2e/procurement.spec.ts`
accepts a seeded supplier quote in the tender workspace and creates the draft PO
in the Procurement workspace; `tests/e2e/reporting.spec.ts` checks the
notification panel, Reporting sections, audit filtering/detail and both export
downloads. The golden path selects a saved customer to prefill the e-invoice. It also verifies that a sales user and an approver have no
nav link and are redirected away from `/ops/workers`. The
mock relay (`mock-n8n-server.mjs`) verifies the outbound HMAC and returns a
signed supplier-quote callback; the deployable production equivalent is
`deploy/n8n/rfq-supplier-workflow.json`. All use ARIA locators (`getByRole`,
`getByLabel`) and a single worker, so the run is deterministic.

Prerequisites: PostgreSQL/MinIO/ClamAV, Alembic migrations, an API on
`127.0.0.1:8000` with `ED25519_APPROVAL_PRIVATE_KEY` and `COMPANY_*` values, and
the fixture rows from `python scripts/seed_e2e.py` (repo root). The seed writes
the git-ignored `tests/e2e/.fixtures.json` consumed by the spec. `CI=true` lets
Playwright start Vite itself; locally an already running dev server is reused.
Reports and traces land in `playwright-report/` and `test-results/`.

## Views

| Route | Content |
|---|---|
| `/login` | Username/password login with an MFA (TOTP) step when the account requires it, plus a forgot-password link. |
| `/forgot-password` | Public username form calling `POST /auth/password/reset/request`; always shows the same confirmation. |
| `/reset-password` | Public token + new-password form calling `POST /auth/password/reset`, with inline validation and success state. |
| `/` | Overview: owner-scoped KPI cards, gross invoiced/paid/outstanding, recent audit events (admin/viewer), ERPNext outbox counts (admin/viewer) and session details. |
| `/tenders` | Paginated tender board with status filter. The header uploads GAEB `.x83`/`.d83` or PDF tenders, shows ClamAV/parse errors and structural warnings, reviews normalized positions and lets an admin reassign the owner. Rows open a detail sheet with metadata, source download, supplier-quote comparison (price, validity, delivery, n8n/dashboard source), manual quote entry, RFQ dispatch, hybrid catalogue matching, linked offers and an offer-creation dialog: choose a persisted quote or manual costs, margin, freight flat, pallet unit/count, live `/price` VAT/totals preview, then `/workflow` draft creation. |
| `/offers` | Paginated offer workspace with status and owner filters, a responsive table, a detail side panel (server VAT breakdown, signature state, line items, approved `.x84` bid download) and the approver/admin four-eyes approval dialog with an optional “Generate e-invoice” section (buyer, Leitweg-ID, due date, IBAN, payment terms). `?offer=<id>` deep-links the side panel (used by the Outbox and offer-creation handoff). |
| `/invoices` | Responsive invoice table with status filter, PDF (ZUGFeRD) and XML (XRechnung) downloads, the admin-only "Mark as paid" dialog and an invoice detail sheet (authoritative totals, line items including freight/pallet surcharges, VAT breakdown, credit-note history with credit-note PDF/XML downloads, document downloads, verified GoBD event timeline, admin-only credit-note/cancel/email dialogs with inline validation). |
| `/procurement` | Sales/admin procurement workspace: container-query KPI grid, accepted-quote → draft PO dialog, PO lifecycle actions (send/confirm/deliver/cancel), delivery progress, supplier-invoice recording with PO-prefilled line quantities/prices, deterministic matching and re-match, an exception queue for total/delivery/quantity/price deviations, and quoted vs actual invoice margin with variance and coverage. Read-only for approvers/viewers. |
| `/reporting` | Commercial reporting: KPI cards, pipeline bars, accessible win/loss donut, tender conversion cards, quoted margin by customer/supplier/tender and paid-vs-overdue invoice aging. Uses a container-query metric grid. Sales users see their own data. |
| `/audit` | Admin-only GoBD audit viewer: event-type/actor/entity/payload/date filters, hash-chain columns, event-detail dialog, and streaming CSV/XLSX export downloads. |
| Header notification center | Bell popover with live counts/links for procurement exceptions, ClamAV quarantines, dead-letter/outbox failures and overdue invoices. Available to admin/approver/viewer. |
| `/ops/outbox` | Operations console for quotation_sync_jobs: status filter, attempts/retry budget, next attempt, last ERPNext error, a Retry action for failed jobs (admin/approver) and a link to the linked offer. |
| `/ops/workers` | Admin-only Workers & Ingestion console: queue-health KPIs, durable worker-command lifecycle with cancel-run, server-paginated/filterable worker-run history with a JSON detail drawer, server-paginated ingestion queue, malware-quarantine and dead-letter tables, manual scrape/ingestion/release-lock dialogs, a read-only portal Probe dialog (portal, 1–5 pages, HTML position counts, warnings/errors, zero-result diagnostics) and Retry/Discard confirmations with cancel-first focus and audit events. |
| `/master-data/suppliers` | Admin-only supplier directory with search/status filters, server pagination, create/edit dialogs including contact rows and primary-contact selection, soft deactivation, and the address book used by RFQ dispatch. |
| `/master-data/customers` | Admin-only customer billing directory with search/status filters, server pagination, create/edit dialogs (address, VAT ID, Leitweg-ID, payment terms, contact), soft deactivation, and e-invoice prefill. |
| `/admin/catalog` | Admin-only catalogue override table with supplier/article filters, pin badges and a dialog to add, replace or clear pinned merge fields. |
| `/settings` | Account security: profile summary, password change, sign-out-everywhere confirmation, TOTP enrolment (QR code, manual secret, otpauth URI), ten one-time recovery codes with mandatory acknowledgment, and a password/code-confirmed disable dialog. Bootstrap-admin accounts see an explanatory notice. |
| `/admin/users` | Admin-only user administration: responsive container-query table (roles, MFA, active/locked status, last login), create-user dialog, role editing, one-time reset-token issuance, and lock/unlock confirmations that focus Cancel first. |

## Auth flow

- `src/lib/api-client.ts` injects `Authorization: Bearer <access token>` and
  refreshes on `401` through `POST /auth/refresh`, retrying the original request.
  Concurrent 401s share a single refresh request (single-flight).
- The access token is kept **in memory only**. The refresh token is an
  `HttpOnly`, `SameSite=Strict` cookie set by the API (`withCredentials: true`),
  so it is never readable from JavaScript and a page reload restores the session
  by calling `/auth/refresh` without a body.
- `403` (RBAC/four-eyes) and `429` (rate limit, with `Retry-After`) produce
  toast notifications; the Axios error normalizes FastAPI `detail` payloads.
- MFA is detected from the `401` + `X-Yasmina-MFA-Required` response header and
  the login form then requests `totp_code`, with a toggle for a single-use
  `recovery_code`.
- Password recovery and self-service password changes use
  `/auth/password/reset/request`, `/auth/password/reset`,
  `/auth/password/change` and `/auth/logout/all`.
- A session-expiry event clears local state and routes back to `/login`.

## Backend endpoints used

- `POST /auth/token`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`
- `POST /auth/password/change`, `POST /auth/password/reset/request`,
  `POST /auth/password/reset`, `POST /auth/logout/all`
- `POST /auth/mfa/setup`, `POST /auth/mfa/verify`, `POST /auth/mfa/disable`
- `GET /auth/users`, `POST /auth/users`,
  `PATCH /auth/users/{username}/roles`,
  `POST /auth/users/{username}/lock|unlock`,
  `POST /auth/users/{username}/password/reset`
- `POST /tenders/upload`, `PATCH /tenders/{id}/owner`,
  `GET /tenders`, `GET /tenders/{id}`, `GET /tenders/{id}/document`
- `POST /tenders/{id}/match`, `POST /tenders/{id}/price`,
  `POST /tenders/{id}/workflow`
- `GET /supplier-quotes?tender_id={id}`, `GET /supplier-quotes/{id}`,
  `POST /supplier-quotes`, `POST /supplier-quotes/{id}/accept`
- `GET /procurement/accepted-quotes`,
  `GET/POST /procurement/purchase-orders`,
  `GET /procurement/purchase-orders/{id}`,
  `POST /procurement/purchase-orders/{id}/status`,
  `GET/POST /procurement/supplier-invoices`,
  `GET /procurement/supplier-invoices/{id}`,
  `POST /procurement/supplier-invoices/{id}/rematch`,
  `GET /procurement/margins[/{offer_id}]`,
  `GET /procurement/health`
- `GET /reporting/summary`, `GET /reporting/margins`, `GET /reporting/aging`
- `GET /ops/notifications`
- `GET /audit/event-types`, `GET /audit/logs`,
  `GET /audit/logs/export?format=csv|xlsx`
- `POST /integrations/rfq/dispatch` with an `Idempotency-Key`
- `GET /offers?tender_id={id}`, `GET /offers` (status/owner/limit/offset),
  `GET /offers/{id}` (includes the server-computed `vat_breakdown`),
  `POST /offers/{id}/x84`, `GET /offers/{id}/x84`
- `POST /approvals/approve` (server signs the offer for the authenticated approver; optional `invoice` block generates the ZUGFeRD/XRechnung artifacts)
- `GET /invoices`, `GET /invoices/{id}` (invoice + line items + events +
  `event_chain_valid`), `GET /invoices/{id}/documents/{zugferd|xrechnung}`,
  `GET /invoices/{id}/credit-notes`,
  `GET /invoices/{id}/credit-notes/{note_id}/documents/{zugferd|xrechnung}`,
  `POST /invoices/{id}/payments`, `POST /invoices/{id}/credits`,
  `POST /invoices/{id}/cancel`, `POST /invoices/{id}/email`
- `GET /ops/summary`, `GET /ops/activity`, `GET /ops/outbox-status`
- `GET /ops/outbox` (status/limit/offset), `POST /ops/outbox/{job_id}/retry`
- `GET /ops/worker-runs` (run_type/status/limit/offset),
  `GET /healthz/worker_runs` (admin alias),
  `POST /ops/worker-runs/scrape`, `POST /ops/worker-runs/ingestion`,
  `GET /ops/worker-commands`, `POST /ops/worker-commands/{id}/cancel`,
  `GET /ops/ingestion` (status/queue/quarantined/limit/offset),
  `POST /ops/ingestion/{job_id}/retry`, `POST /ops/ingestion/{job_id}/discard`,
  `POST /ops/ingestion/release-stale`, `GET /ops/portals`,
  `POST /ops/portals/{portal_id}/probe`
- `GET/POST /suppliers`, `GET/PUT/DELETE /suppliers/{id}`,
  `GET/POST /customers`, `GET/PUT/DELETE /customers/{id}`
- `GET /admin/catalog/overrides`, `POST /admin/catalog/overrides`

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Backend base URL. Keep `/api/v1` for the Vite proxy, or set the absolute URL (e.g. `http://127.0.0.1:8000/api/v1`) and allow the dashboard origin via backend `CORS_ALLOWED_ORIGINS`. Cookie auth requires the dashboard and API to be same-site. |

## Current scope

- [x] Branded login with MFA step
- [x] Protected app shell (sidebar, header, user menu, mobile navigation)
- [x] Typed API layer + auth context + React Query provider
- [x] Cookie-based refresh sessions with in-memory access tokens
- [x] Overview KPIs, billing totals, audit activity feed and ERPNext outbox widget
- [x] Paginated tender board, status filter and detail sheet
- [x] Tender upload with file validation, ClamAV/parse error states and structural warnings
- [x] Parsed-position review, parser-warning display and admin owner assignment
- [x] Offer creation with per-position supplier costs, margin/freight/pallet controls and server pricing preview
- [x] Persisted supplier-quote selection with coverage warnings and manual fallback costs
- [x] Supplier-quote comparison and dashboard manual quote entry
- [x] RFQ dispatch with idempotency, pending-TTL and n8n error states
- [x] Approved GAEB `.x84` bid download from offer and tender details
- [x] Source-document download, catalogue matching scores and linked offer pricing
- [x] Responsive invoice table with ZUGFeRD/XRechnung downloads
- [x] Admin-only "Mark as paid" payment dialog
- [x] Admin catalogue override table and pin/replace/clear dialog
- [x] Outbox console with status filter, pagination and a failed-job Retry action
- [x] Admin-only Workers & Ingestion console with server-paginated/filterable worker runs, durable worker commands with cancel-run, queue/quarantine/DLQ tables, a run-detail drawer, manual worker actions, retry/discard confirmations and a read-only portal probe with HTML position counts
- [x] Admin-only Master Data directories for suppliers/contacts and customers, with server-side search/pagination and soft deactivation
- [x] RFQ address-book supplier selection and saved-customer e-invoice prefill
- [x] Procurement workspace with accepted-quote PO generation, PO lifecycle, supplier-invoice matching, exception queue and actual-vs-quoted margin
- [x] Container-query procurement metric/exception layouts and responsive PO/invoice/margin tables
- [x] Reporting workspace with accessible pipeline/win-loss/conversion/margin/aging visualisations
- [x] Admin GoBD audit viewer with filters, detail dialog and streaming CSV/XLSX downloads
- [x] Header notification center for procurement, ClamAV, dead-letter, outbox and overdue alerts
- [x] Playwright reporting/audit coverage with real CSV/XLSX download assertions
- [x] Playwright procurement journey (accepted supplier quote → generated PO)
- [x] Playwright operations/master-data coverage for pagination, run details, command cancellation, CRUD, RFQ prefill and Sales/Approver denial
- [x] Offer workspace with status/owner filters, detail side panel and four-eyes approval dialog
- [x] Optional e-invoice issuance (ZUGFeRD/XRechnung) from the approval dialog with inline validation and 422 recovery
- [x] Playwright golden-path E2E (approval + credit note + invoice + payment + outbox retry) in CI
- [x] Settings page for TOTP enrolment, QR/manual secret and one-time recovery codes
- [x] Recovery-code login fallback for MFA-enabled accounts
- [x] Admin Users console (create, roles, reset token, lock/unlock) with a responsive table
- [x] Playwright admin flow (create/lock user) and `/admin/users` RBAC redirect
- [x] Forgot-password and reset-token pages plus Playwright recovery coverage
- [x] Account settings password change and sign-out-everywhere
- [x] Invoice detail cancellation, SMTP delivery and credit-note PDF/XML downloads
- [x] Responsive container-query invoice table and invoice detail sheet with totals, VAT and downloads
- [x] Line items (including freight/pallet surcharge badges) in offer and invoice detail views
- [x] Admin credit-note dialog with amount/reason validation, cancel-first focus and in-place status refresh
- [x] GoBD hash-chain event timeline with verified/broken chain state and per-event hashes

## Production edge

The repository ships `Dockerfile.frontend` and `Caddyfile`; the production
Compose stack serves the built SPA from Caddy at `https://localhost` with
`tls internal` and proxies `/api/*`, `/docs*` and `/openapi.json` to the API.
See the root README for the certificate-acceptance steps.
