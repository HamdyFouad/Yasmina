"""Unit tests for admin-triggered worker process launches."""

from __future__ import annotations

from typing import Any

import pytest

from src.admin import _parser
from src.config import Settings
from src.services.worker_command import WorkerCommandError, WorkerCommandLauncher


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        app_env="test",
        jwt_secret_key="worker-command-secret-with-32-bytes",
    )


class FakeProcess:
    pid = 4321

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        self.command = command
        self.kwargs = kwargs


def test_scrape_and_ingestion_commands_are_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[FakeProcess] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, **kwargs)
        seen.append(process)
        return process

    monkeypatch.setattr("src.services.worker_command.subprocess.Popen", fake_popen)
    launcher = WorkerCommandLauncher(make_settings())

    scrape = launcher.launch_scrape(portal="bayern", max_pages=2)
    ingestion = launcher.launch_ingestion(limit=5)

    assert scrape.pid == 4321
    assert scrape.command[-4:] == ("--portal", "bayern", "--max-pages", "2")
    assert ingestion.command[-4:] == ("src.worker", "process", "--limit", "5")
    assert all(process.kwargs["stdout"] is not None for process in seen)


def test_unknown_portal_is_rejected() -> None:
    launcher = WorkerCommandLauncher(make_settings())

    with pytest.raises(WorkerCommandError, match="unknown portal"):
        launcher.launch_scrape(portal="does-not-exist")


def test_disabled_portal_is_rejected() -> None:
    launcher = WorkerCommandLauncher(make_settings())

    with pytest.raises(WorkerCommandError, match="disabled"):
        launcher.launch_scrape(portal="nrw")


def test_worker_run_retention_cli_arguments_parse() -> None:
    args = _parser().parse_args(["purge-worker-runs", "--retention-days", "30", "--dry-run", "--json"])

    assert args.command == "purge-worker-runs"
    assert args.retention_days == 30
    assert args.dry_run is True
    assert args.as_json is True
