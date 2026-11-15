#!/usr/bin/env bash
# Staging rollout executed on the Hetzner VPS by .github/workflows/deploy-staging.yml.
#
# Order: registry login -> database backup -> pull -> migrate -> restart ->
# smoke test. A failed smoke test restores the previous IMAGE_TAG; database
# migrations are never automatically reversed (the pre-deploy dump is the
# recovery point if a migration must be rolled back manually).
#
# Required environment:
#   DEPLOY_DIR, IMAGE_TAG, REGISTRY, IMAGE_OWNER,
#   STAGING_BASE_URL, SMOKE_USERNAME, SMOKE_PASSWORD,
#   GHCR_USER, GHCR_READ_TOKEN

set -euo pipefail

: "${IMAGE_TAG:?IMAGE_TAG is required}"
: "${REGISTRY:?REGISTRY is required}"
: "${IMAGE_OWNER:?IMAGE_OWNER is required}"
: "${STAGING_BASE_URL:?STAGING_BASE_URL is required}"
: "${SMOKE_USERNAME:?SMOKE_USERNAME is required}"
: "${SMOKE_PASSWORD:?SMOKE_PASSWORD is required}"

DEPLOY_DIR="${DEPLOY_DIR:-/opt/yasmina/staging}"
IMAGE_OWNER="$(printf '%s' "${IMAGE_OWNER}" | tr '[:upper:]' '[:lower:]')"
REGISTRY="${REGISTRY%/}"
cd "${DEPLOY_DIR}"

if [ ! -f .env.staging ]; then
	echo "ERROR: ${DEPLOY_DIR}/.env.staging is missing" >&2
	exit 1
fi

COMPOSE=(docker compose --env-file .env.staging -f docker-compose.staging.yml)
PREVIOUS_TAG="$(cat .deployed_tag 2>/dev/null || true)"
BACKUP_DIR="${DEPLOY_DIR}/backups"
mkdir -p "${BACKUP_DIR}" scripts

if [ -n "${GHCR_READ_TOKEN:-}" ]; then
	if ! printf '%s' "${GHCR_READ_TOKEN}" | docker login ghcr.io -u "${GHCR_USER:-github-actions}" --password-stdin >/dev/null; then
		echo "ERROR: GHCR login failed" >&2
		exit 1
	fi
fi

echo "Deploying image tag ${IMAGE_TAG} (previous: ${PREVIOUS_TAG:-none})"

# Backup first. On the very first rollout the postgres container does not exist
# yet, so this is skipped; subsequent rollouts always produce a gzip dump.
if [ -n "$("${COMPOSE[@]}" ps -q postgres 2>/dev/null)" ]; then
	backup_file="${BACKUP_DIR}/pre-${IMAGE_TAG}-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
	"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "${backup_file}"
	chmod 600 "${backup_file}"
	echo "Database backup: ${backup_file}"
else
	echo "WARNING: postgres is not running yet; skipping pre-deploy pg_dump"
fi

IMAGE_TAG="${IMAGE_TAG}" REGISTRY="${REGISTRY}" IMAGE_OWNER="${IMAGE_OWNER}" \
	"${COMPOSE[@]}" pull

# Apply migrations through the new image while the old containers keep serving.
IMAGE_TAG="${IMAGE_TAG}" REGISTRY="${REGISTRY}" IMAGE_OWNER="${IMAGE_OWNER}" \
	"${COMPOSE[@]}" run --rm --no-deps api alembic upgrade head

IMAGE_TAG="${IMAGE_TAG}" REGISTRY="${REGISTRY}" IMAGE_OWNER="${IMAGE_OWNER}" \
	"${COMPOSE[@]}" up -d --remove-orphans

if ! BASE_URL="${STAGING_BASE_URL}" SMOKE_USERNAME="${SMOKE_USERNAME}" SMOKE_PASSWORD="${SMOKE_PASSWORD}" \
	bash scripts/smoke_test.sh; then
	echo "ERROR: smoke test failed for ${IMAGE_TAG}" >&2
	if [ -n "${PREVIOUS_TAG}" ]; then
		echo "Rolling back containers to ${PREVIOUS_TAG}; database migration is NOT reversed" >&2
		IMAGE_TAG="${PREVIOUS_TAG}" REGISTRY="${REGISTRY}" IMAGE_OWNER="${IMAGE_OWNER}" \
			"${COMPOSE[@]}" pull || true
		IMAGE_TAG="${PREVIOUS_TAG}" REGISTRY="${REGISTRY}" IMAGE_OWNER="${IMAGE_OWNER}" \
			"${COMPOSE[@]}" up -d --remove-orphans
		BASE_URL="${STAGING_BASE_URL}" SMOKE_USERNAME="${SMOKE_USERNAME}" SMOKE_PASSWORD="${SMOKE_PASSWORD}" \
			bash scripts/smoke_test.sh || true
	else
		echo "ERROR: no previous image tag exists for automatic rollback" >&2
	fi
	exit 1
fi

# Persist the successful tag for host restarts and future rollbacks.
set_env() {
	key="$1"
	value="$2"
	if grep -q "^${key}=" .env.staging; then
		sed -i "s|^${key}=.*|${key}=${value}|" .env.staging
	else
		printf '%s=%s\n' "${key}" "${value}" >> .env.staging
	fi
}

set_env "IMAGE_TAG" "${IMAGE_TAG}"
set_env "REGISTRY" "${REGISTRY}"
set_env "IMAGE_OWNER" "${IMAGE_OWNER}"
printf '%s\n' "${IMAGE_TAG}" > .deployed_tag

echo "Deployment succeeded: ${IMAGE_TAG}"
