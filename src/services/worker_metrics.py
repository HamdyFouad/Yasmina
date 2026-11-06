"""Operational snapshot used by ``GET /healthz/worker``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.repositories.ingestion_repository import TenderIngestionJobRepository
from src.repositories.worker_run_repository import WorkerRunRepository

__all__ = ["WorkerHealthService"]


class WorkerHealthService:
    """Combine worker run history with the current outbox queue state."""

    def __init__(
        self,
        runs: WorkerRunRepository,
        jobs: TenderIngestionJobRepository,
    ) -> None:
        self.runs = runs
        self.jobs = jobs

    async def snapshot(self) -> dict[str, Any]:
        latest = await self.runs.latest_by_type()
        run_counts = await self.runs.status_counts()
        outbox = await self.jobs.status_counts()
        recent = await self.runs.recent(limit=10)

        degraded = outbox.get("failed", 0) > 0 or any(
            run.status in {"failed", "cancelled"} for run in latest.values()
        )
        return {
            "status": "degraded" if degraded else "ok",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runs": {
                "latest": {run_type: run.as_dict() for run_type, run in latest.items()},
                "counts": run_counts,
            },
            "outbox": outbox,
            "recent": [run.as_dict() for run in recent],
        }
