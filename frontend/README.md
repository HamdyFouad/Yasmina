# Yasmina Day 2 Operations Dashboard

Operations console for the Yasmina API: authentication, an overview with KPIs and
the audit activity feed, the tender pipeline with matching/pricing detail, and
invoice billing with compliant document downloads.

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
admin login → invoice list → payment → outbox retry. `tests/e2e/admin-users.spec.ts`
logs in as the seeded admin, creates an account in the Users console, locks it
through the cancel-focused confirmation, and checks that an approver is
redirected away from `/admin/users`. Both use ARIA locators (`getByRole`,
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
| `/login` | Username/password login with an MFA (TOTP) step when the account requires it. |
| `/` | Overview: owner-scoped KPI cards, gross invoiced/paid/outstanding, recent audit events (admin/viewer), ERPNext outbox counts (admin/viewer) and session details. |
| `/tenders` | Paginated tender board with status filter. Rows open a detail sheet with metadata, the original GAEB/PDF download, hybrid catalogue matching and linked pricing offers with their line/VAT breakdown and totals. |
| `/offers` | Paginated offer workspace with status and owner filters, a responsive table, a detail side panel (server VAT breakdown, signature state, line items) and the approver/admin four-eyes approval dialog with an optional “Generate e-invoice” section (buyer, Leitweg-ID, due date, IBAN, payment terms). `?offer=<id>` deep-links the side panel (used by the Outbox). |
| `/invoices` | Paginated invoice table with status filter, PDF (ZUGFeRD) and XML (XRechnung) downloads, and the admin-only "Mark as paid" dialog. |
| `/ops/outbox` | Operations console for quotation_sync_jobs: status filter, attempts/retry budget, next attempt, last ERPNext error, a Retry action for failed jobs (admin/approver) and a link to the linked offer. |
| `/admin/catalog` | Admin-only catalogue override table with supplier/article filters, pin badges and a dialog to add, replace or clear pinned merge fields. |
| `/settings` | Account security: profile summary, TOTP enrolment (QR code, manual secret, otpauth URI), ten one-time recovery codes with mandatory acknowledgment, and a password/code-confirmed disable dialog. Bootstrap-admin accounts see an explanatory notice. |
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
- A session-expiry event clears local state and routes back to `/login`.

## Backend endpoints used

- `POST /auth/token`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`
- `POST /auth/mfa/setup`, `POST /auth/mfa/verify`, `POST /auth/mfa/disable`
- `GET /auth/users`, `POST /auth/users`,
  `PATCH /auth/users/{username}/roles`,
  `POST /auth/users/{username}/lock|unlock`,
  `POST /auth/users/{username}/password/reset`
- `GET /tenders`, `GET /tenders/{id}`, `GET /tenders/{id}/document`
- `POST /tenders/{id}/match`
- `GET /offers?tender_id={id}`, `GET /offers` (status/owner/limit/offset),
  `GET /offers/{id}` (includes the server-computed `vat_breakdown`)
- `POST /approvals/approve` (server signs the offer for the authenticated approver; optional `invoice` block generates the ZUGFeRD/XRechnung artifacts)
- `GET /invoices`, `GET /invoices/{id}/documents/{zugferd|xrechnung}`,
  `POST /invoices/{id}/payments`
- `GET /ops/summary`, `GET /ops/activity`, `GET /ops/outbox-status`
- `GET /ops/outbox` (status/limit/offset), `POST /ops/outbox/{job_id}/retry`
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
- [x] Source-document download, catalogue matching scores and linked offer pricing
- [x] Paginated invoice table with ZUGFeRD/XRechnung downloads
- [x] Admin-only "Mark as paid" payment dialog
- [x] Admin catalogue override table and pin/replace/clear dialog
- [x] Outbox console with status filter, pagination and a failed-job Retry action
- [x] Offer workspace with status/owner filters, detail side panel and four-eyes approval dialog
- [x] Optional e-invoice issuance (ZUGFeRD/XRechnung) from the approval dialog with inline validation and 422 recovery
- [x] Playwright golden-path E2E (approval + invoice + payment + outbox retry) in CI
- [x] Settings page for TOTP enrolment, QR/manual secret and one-time recovery codes
- [x] Recovery-code login fallback for MFA-enabled accounts
- [x] Admin Users console (create, roles, reset token, lock/unlock) with a responsive table
- [x] Playwright admin flow (create/lock user) and `/admin/users` RBAC redirect

## Production edge

The repository ships `Dockerfile.frontend` and `Caddyfile`; the production
Compose stack serves the built SPA from Caddy at `https://localhost` with
`tls internal` and proxies `/api/*`, `/docs*` and `/openapi.json` to the API.
See the root README for the certificate-acceptance steps.
