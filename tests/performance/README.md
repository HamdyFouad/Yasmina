# Load & performance testing

`locustfile.py` simulates concurrent authenticated users that log in, poll
`/auth/me`, hit `/healthz` and run the pgvector-backed
`POST /api/v1/tenders/{id}/match` for a seeded tender. The point is to verify
that PostgreSQL connection handling, the HNSW catalogue query and the Redis
rate-limit buckets stay healthy under load and that 429s only appear when the
configured budgets are exhausted.

## 1. Prepare the stack

```powershell
docker compose up -d postgres minio clamav
# Production-like distributed rate limiting (optional but recommended):
docker run -d --name yasmina-redis -p 127.0.0.1:6379:6379 redis:7-alpine
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
python -m alembic upgrade head
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
```

Seed at least one catalogue article and one tender with items (for example via
`yasmina-admin seed-catalog` and the normal GAEB upload endpoint). A tender with
one or more positions is required for the match scenario; the tender UUID is
returned by `POST /api/v1/tenders/upload`.

Create a load-test user (MFA disabled on purpose so the profile measures the hot
path instead of TOTP entry):

```powershell
$admin = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/auth/token `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"<admin-password>"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/auth/users `
  -Headers @{ Authorization = "Bearer $($admin.access_token)" } `
  -ContentType "application/json" `
  -Body '{"username":"loadtest","password":"Load-Test-Password-42!","roles":["sales"]}'
```

## 2. Run Locust

```powershell
python -m pip install -e ".[performance]"

$env:YASMINA_LOAD_USERNAME = "loadtest"
$env:YASMINA_LOAD_PASSWORD = "Load-Test-Password-42!"
$env:YASMINA_LOAD_TENDER_ID = "<tender-uuid>"

locust -f tests/performance/locustfile.py --host http://127.0.0.1:8000 `
  --users 50 --spawn-rate 5 --run-time 5m --headless --html load-report.html
```

Open <http://127.0.0.1:8089> instead of `--headless` for the interactive UI.
`YASMINA_LOAD_MATCH_LIMIT` (default 5) controls the number of matches requested
per call.

## 3. What to watch

- **Locust report** — p95/p99 latency for `POST /tenders/[id]/match`, failure
  ratio and requests/second.
- **`GET /metrics`** — `yasmina_http_request_duration_seconds` percentiles,
  `yasmina_http_errors_total` (expected 429s appear there) and
  `yasmina_workflow_*` if approval workflows are exercised.
- **PostgreSQL** — `SELECT count(*), state FROM pg_stat_activity GROUP BY state;`
  should stay well below `max_connections` (100 in the prod compose file), and
  `pg_stat_database` should not show rising connection errors.
- **Redis** — `redis-cli INFO commandstats` shows `eval` throughput; the bucket
  keys are `yasmina:ratelimit:<scope>:<client-ip>` with a 60 s TTL. Watch
  `INFO memory` for unbounded key growth.
- **Containers** — `docker stats` to compare CPU/memory against the prod compose
  limits.

Raise `RATE_LIMIT_AUTH_PER_MINUTE` / `RATE_LIMIT_BUSINESS_PER_MINUTE` when you
want to measure throughput instead of the limiter, then run a second pass with
production limits to confirm clean 429 responses and `Retry-After` headers.
