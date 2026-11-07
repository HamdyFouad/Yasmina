"""Locust load profile for the Yasmina API.

Scenarios per virtual user:
  * authenticate against ``POST /api/v1/auth/token`` on start,
  * call ``GET /api/v1/auth/me`` (JWT + Redis rate-limit path),
  * call ``POST /api/v1/tenders/{id}/match`` which runs the pgvector catalogue
    query and hybrid ranking for every tender position,
  * poll ``/healthz``.

Configure with environment variables (never commit credentials):

    YASMINA_LOAD_USERNAME      database user (MFA must be disabled)
    YASMINA_LOAD_PASSWORD      password for that user
    YASMINA_LOAD_TENDER_ID     UUID of a tender with items; required for match

Example:

    locust -f tests/performance/locustfile.py \
        --host http://127.0.0.1:8000 \
        --users 50 --spawn-rate 5 --run-time 5m --headless
"""

from __future__ import annotations

import logging
import os

from locust import HttpUser, between, events, task

logger = logging.getLogger("yasmina.loadtest")

USERNAME = os.getenv("YASMINA_LOAD_USERNAME", "loadtest")
PASSWORD = os.getenv("YASMINA_LOAD_PASSWORD", "")
TENDER_ID = os.getenv("YASMINA_LOAD_TENDER_ID", "")
MATCH_LIMIT = int(os.getenv("YASMINA_LOAD_MATCH_LIMIT", "5"))


@events.test_start.add_listener
def _announce_configuration(environment, **_kwargs) -> None:  # type: ignore[no-untyped-def]
    if not PASSWORD:
        raise RuntimeError("YASMINA_LOAD_PASSWORD must be set for the load test user")
    if not TENDER_ID:
        logger.warning(
            "YASMINA_LOAD_TENDER_ID is not set: virtual users will only exercise "
            "authentication/me/health and skip the pgvector match scenario"
        )
    print(f"Load test target user={USERNAME} tender={TENDER_ID or '<unset>'}")


class YasminaUser(HttpUser):
    """Authenticated user hitting the read-heavy catalogue/matching paths."""

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        response = self.client.post(
            "/api/v1/auth/token",
            json={"username": USERNAME, "password": PASSWORD},
            name="POST /api/v1/auth/token",
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"load-test login failed with HTTP {response.status_code}: {response.text[:200]}"
            )
        self.access_token: str = response.json()["access_token"]
        self.authorization = {"Authorization": f"Bearer {self.access_token}"}

    @task(2)
    def current_user(self) -> None:
        self.client.get(
            "/api/v1/auth/me",
            headers=self.authorization,
            name="GET /api/v1/auth/me",
        )

    @task(1)
    def health(self) -> None:
        self.client.get("/healthz", name="GET /healthz")

    @task(4)
    def match_catalog(self) -> None:
        if not TENDER_ID:
            return
        self.client.post(
            f"/api/v1/tenders/{TENDER_ID}/match",
            json={"limit": MATCH_LIMIT},
            headers=self.authorization,
            name="POST /api/v1/tenders/[id]/match",
        )
