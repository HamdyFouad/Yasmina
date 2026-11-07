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
MFA enabled, the page reveals the TOTP field after the password step.

Scripts:

```powershell
npm run dev        # Vite dev server (port 5173)
npm run lint       # oxlint
npm run typecheck  # tsc -b
npm run build      # type-check + production bundle
npm run preview    # serve the built bundle
```

## Views

| Route | Content |
|---|---|
| `/login` | Username/password login with an MFA (TOTP) step when the account requires it. |
| `/` | Overview: owner-scoped KPI cards, gross invoiced/paid/outstanding, recent audit events (admin/viewer) and session details. |
| `/tenders` | Paginated tender board with status filter. Rows open a detail sheet with metadata, the original GAEB/PDF download, hybrid catalogue matching and linked pricing offers with their line/VAT breakdown and totals. |
| `/invoices` | Paginated invoice table with status filter, PDF (ZUGFeRD) and XML (XRechnung) downloads, and the admin-only "Mark as paid" dialog. |

The `Offers` navigation entry is still a placeholder; offer pricing is currently
shown inside the tender detail sheet, filtered with `GET /offers?tender_id=...`.

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
  the login form then requests `totp_code`.
- A session-expiry event clears local state and routes back to `/login`.

## Backend endpoints used

- `POST /auth/token`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`
- `GET /tenders`, `GET /tenders/{id}`, `GET /tenders/{id}/document`
- `POST /tenders/{id}/match`
- `GET /offers?tender_id={id}`
- `GET /invoices`, `GET /invoices/{id}/documents/{zugferd|xrechnung}`,
  `POST /invoices/{id}/payments`
- `GET /ops/summary`, `GET /ops/activity`

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Backend base URL. Keep `/api/v1` for the Vite proxy, or set the absolute URL (e.g. `http://127.0.0.1:8000/api/v1`) and allow the dashboard origin via backend `CORS_ALLOWED_ORIGINS`. Cookie auth requires the dashboard and API to be same-site. |

## Current scope

- [x] Branded login with MFA step
- [x] Protected app shell (sidebar, header, user menu, mobile navigation)
- [x] Typed API layer + auth context + React Query provider
- [x] Cookie-based refresh sessions with in-memory access tokens
- [x] Overview KPIs, billing totals and audit activity feed
- [x] Paginated tender board, status filter and detail sheet
- [x] Source-document download, catalogue matching scores and linked offer pricing
- [x] Paginated invoice table with ZUGFeRD/XRechnung downloads
- [x] Admin-only "Mark as paid" payment dialog
- [ ] Offer workspace beyond the tender detail sheet
- [ ] Browser end-to-end tests and a production static-host deployment
