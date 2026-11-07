# Yasmina Day 2 Operations Dashboard

Read-only operations console for the Yasmina API: authentication, overview, tenders,
catalogue matching/pricing and invoice billing. This first slice is the frontend
foundation (shell, auth flow, typed API client); the detailed views follow once the
tender read endpoints land.

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

## Auth flow

- `src/lib/api-client.ts` injects `Authorization: Bearer <access token>` and
  refreshes on `401` through `POST /auth/refresh`, retrying the original request.
  Concurrent 401s share a single refresh request (single-flight).
- Access token is kept **in memory only**. The refresh token is stored in
  `sessionStorage` for tab-scoped reload survival because the backend returns it
  in the JSON body instead of an HttpOnly cookie. Moving it to an HttpOnly cookie
  is the recommended backend follow-up.
- `403` (RBAC/four-eyes) and `429` (rate limit, with `Retry-After`) produce
  toast notifications; the Axios error normalizes FastAPI `detail` payloads.
- MFA is detected from the `401` + `X-Yasmina-MFA-Required` response header and
  the login form then requests `totp_code`.
- A session-expiry event clears local state and routes back to `/login`.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Backend base URL. Keep `/api/v1` for the Vite proxy, or set the absolute URL (e.g. `http://127.0.0.1:8000/api/v1`) and allow the dashboard origin via backend `CORS_ALLOWED_ORIGINS`. |

## Current scope

- [x] Branded login with MFA step
- [x] Protected app shell (sidebar, header, user menu, mobile navigation)
- [x] Typed API layer + auth context + React Query provider
- [x] Overview skeleton with live invoice/offer counts
- [ ] Overview activity feed and tender KPIs (needs `GET /tenders` and an audit stream)
- [ ] Tender table, pipeline filters and detail drawer
- [ ] Matching/pricing panel
- [ ] Paginated invoice table, document downloads and admin "mark as paid"
