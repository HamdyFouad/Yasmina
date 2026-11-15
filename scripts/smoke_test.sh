#!/usr/bin/env bash
# Post-deployment smoke test for the staging edge.
#
# Required environment:
#   STAGING_BASE_URL (or BASE_URL), SMOKE_USERNAME, SMOKE_PASSWORD
#
# Checks /healthz through Caddy and one authenticated API endpoint. The deploy
# script invokes this after `docker compose up`; a non-zero exit triggers an
# automatic image-tag rollback.

set -euo pipefail

BASE_URL="${STAGING_BASE_URL:-${BASE_URL:-https://staging.yasmina.bayerclean.net}}"
BASE_URL="${BASE_URL%/}"

: "${SMOKE_USERNAME:?SMOKE_USERNAME is required}"
: "${SMOKE_PASSWORD:?SMOKE_PASSWORD is required}"

# JSON parsing needs a working interpreter; Ubuntu ships python3, but the
# fallback keeps the script usable on minimal images and Windows Git Bash.
if [ -z "${PYTHON_BIN:-}" ]; then
	for candidate in python3 python; do
		if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c 'import json' >/dev/null 2>&1; then
			PYTHON_BIN="${candidate}"
			break
		fi
	done
fi
if [ -z "${PYTHON_BIN:-}" ]; then
	echo "SMOKE FAILED: python3 with the json module is required" >&2
	exit 1
fi

healthy=0
for _attempt in $(seq 1 30); do
	if curl -fsS --max-time 5 "${BASE_URL}/healthz" >/dev/null 2>&1; then
		healthy=1
		break
	fi
	sleep 2
done

if [ "${healthy}" -ne 1 ]; then
	echo "SMOKE FAILED: ${BASE_URL}/healthz did not become healthy" >&2
	exit 1
fi

login_body=$(SMOKE_USERNAME="${SMOKE_USERNAME}" SMOKE_PASSWORD="${SMOKE_PASSWORD}" "${PYTHON_BIN}" - <<'PY'
import json
import os

print(json.dumps({
    "username": os.environ["SMOKE_USERNAME"],
    "password": os.environ["SMOKE_PASSWORD"],
}))
PY
)

login_response=$(curl -fsS --max-time 10 \
	-X POST "${BASE_URL}/api/v1/auth/token" \
	-H 'Content-Type: application/json' \
	--data "${login_body}") || {
	echo "SMOKE FAILED: login request failed" >&2
	exit 1
}

access_token=$(printf '%s' "${login_response}" | "${PYTHON_BIN}" -c '
import json
import sys

try:
    print(json.load(sys.stdin)["access_token"])
except Exception:
    raise SystemExit("SMOKE FAILED: login response has no access_token")
')

me_response=$(curl -fsS --max-time 10 \
	"${BASE_URL}/api/v1/auth/me" \
	-H "Authorization: Bearer ${access_token}") || {
	echo "SMOKE FAILED: authenticated /auth/me request failed" >&2
	exit 1
}

printf '%s' "${me_response}" | "${PYTHON_BIN}" -c '
import json
import sys

try:
    subject = json.load(sys.stdin)["subject"]
except Exception:
    raise SystemExit("SMOKE FAILED: authenticated response is invalid")
if not subject:
    raise SystemExit("SMOKE FAILED: authenticated response has no subject")
print(f"authenticated subject: {subject}")
'

echo "SMOKE PASSED: ${BASE_URL}"
