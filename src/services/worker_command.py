"""Launch bounded worker commands from the admin operations console."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from src.config import Settings
from src.scrapers.portal_config import PortalConfigError, load_portal_targets

__all__ = [
    "WorkerCommandError",
    "WorkerCommandLauncher",
    "WorkerCommandResult",
]


class WorkerCommandError(ValueError):
    """Raised when a manual worker command cannot be started."""


@dataclass(frozen=True, slots=True)
class WorkerCommandResult:
    action: str
    command: tuple[str, ...]
    pid: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": True,
            "action": self.action,
            "command": list(self.command),
            "pid": self.pid,
        }


class WorkerCommandLauncher:
    """Spawn a detached one-shot worker for an admin-triggered run.

    Portal/domain advisory locks still protect against duplicate workers. The
    API only starts a process and returns immediately; progress is visible in
    ``worker_runs`` and the ingestion queue.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def launch_scrape(
        self,
        *,
        portal: str = "all",
        max_pages: int | None = 1,
        command_id: str | None = None,
    ) -> WorkerCommandResult:
        self._validate_portal(portal)
        command = [sys.executable, "-m", "src.worker", "scrape", "--portal", portal]
        if max_pages is not None:
            command.extend(["--max-pages", str(max_pages)])
        if command_id is not None:
            command.extend(["--command-id", command_id])
        return self._spawn("scrape", command)

    def launch_ingestion(
        self,
        *,
        limit: int = 10,
        command_id: str | None = None,
    ) -> WorkerCommandResult:
        if not 1 <= limit <= 50:
            raise WorkerCommandError("limit must be between 1 and 50")
        command = [
            sys.executable,
            "-m",
            "src.worker",
            "process",
            "--limit",
            str(limit),
        ]
        if command_id is not None:
            command.extend(["--command-id", command_id])
        return self._spawn("ingestion", command)

    def terminate(self, pid: int) -> bool:
        """Send a graceful termination request to a running command process."""

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        except OSError as exc:  # pragma: no cover - platform-specific permission errors
            raise WorkerCommandError(f"could not terminate worker process {pid}: {exc}") from exc
        return True

    def _validate_portal(self, portal: str) -> None:
        if portal == "all":
            return
        try:
            targets = load_portal_targets(self.settings.portal_config_file, enabled_only=False)
        except PortalConfigError as exc:
            raise WorkerCommandError(f"portal catalogue unavailable: {exc}") from exc
        if not any(target.id == portal for target in targets):
            raise WorkerCommandError(f"unknown portal {portal!r}")
        target = next(target for target in targets if target.id == portal)
        if not target.enabled:
            raise WorkerCommandError(
                f"portal {portal!r} is disabled; enable it in portals.yaml before triggering a run"
            )

    def _spawn(self, action: str, command: list[str]) -> WorkerCommandResult:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:  # pragma: no cover - production and CI are POSIX
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise WorkerCommandError(f"could not start {action} worker: {exc}") from exc
        return WorkerCommandResult(action=action, command=tuple(command), pid=process.pid)
