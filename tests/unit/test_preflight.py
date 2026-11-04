"""Preflight startup-check tests (no external services required)."""

from __future__ import annotations

import asyncio

from src.config import Settings
from src.preflight import (
    CheckResult,
    PreflightOptions,
    PreflightReport,
    check_clamav,
    check_configuration,
    check_filesystem,
    check_minio,
    parse_args,
    run_preflight,
)


def make_settings(**overrides: object) -> Settings:
    overrides.setdefault("database_url", None)
    return Settings(_env_file=None, **overrides)


def test_parse_args() -> None:
    args = parse_args(["--json", "--skip", "postgres", "--timeout", "2.5", "--require-n8n"])

    assert args.json is True
    assert args.skip == ["postgres"]
    assert args.timeout == 2.5
    assert args.require_n8n is True


def test_report_failure_detection() -> None:
    report = PreflightReport(
        "local",
        (
            CheckResult("configuration", "ok", "fine"),
            CheckResult("postgres", "fail", "down"),
            CheckResult("n8n", "warn", "optional"),
        ),
    )

    assert report.failed is True
    assert report.warnings == 1
    assert report.to_dict()["ok"] is False


def test_production_requires_hitl_guardrails() -> None:
    settings = make_settings(app_env="production", app_secret_key="a-real-secret")
    results = check_configuration(settings)

    assert any(result.status == "fail" for result in results)
    assert any("ED25519_APPROVAL_PUBLIC_KEY" in result.detail for result in results)

    settings = make_settings(
        app_env="production",
        app_secret_key="a-real-secret",
        ed25519_approval_public_key="ab" * 32,
        approval_signature_required=False,
    )
    results = check_configuration(settings)

    assert any("APPROVAL_SIGNATURE_REQUIRED" in result.detail for result in results)


def test_local_configuration_only_warns_about_missing_optional_keys() -> None:
    results = check_configuration(make_settings())

    assert all(result.status != "fail" for result in results)
    assert any(result.status == "warn" for result in results)


def test_filesystem_probe_creates_and_cleans(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(gaeb_export_dir=workspace_tmp / "exports" / "gaeb")
    result = check_filesystem(settings)

    assert result.status == "ok"
    assert (workspace_tmp / "exports" / "gaeb").is_dir()
    assert not (workspace_tmp / "exports" / "gaeb" / ".preflight-write-test").exists()


def test_all_services_can_be_skipped(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    settings = make_settings(gaeb_export_dir=workspace_tmp / "gaeb")
    options = PreflightOptions(skip=frozenset({"postgres", "minio", "clamav", "n8n"}))

    results = asyncio.run(run_preflight(settings, options))
    by_name = {result.name: result for result in results}

    assert by_name["postgres"].status == "skipped"
    assert by_name["postgres.schema"].status == "skipped"
    assert by_name["minio"].status == "skipped"
    assert by_name["clamav"].status == "skipped"
    assert by_name["n8n"].status == "skipped"
    assert not any(result.status == "fail" for result in results)


def test_minio_check_reports_authentication_failures(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("src.preflight._http_probe", lambda url, timeout: (True, "HTTP 200"))
    monkeypatch.setattr("src.preflight._minio_bucket_check", lambda settings: (False, "S3 AccessDenied"))
    settings = make_settings()

    result = asyncio.run(check_minio(settings, PreflightOptions()))

    assert result.status == "fail"
    assert "AccessDenied" in result.detail


def test_minio_check_passes_with_authenticated_bucket(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("src.preflight._http_probe", lambda url, timeout: (True, "HTTP 200"))
    monkeypatch.setattr(
        "src.preflight._minio_bucket_check",
        lambda settings: (True, "authenticated; bucket 'yasmina' accessible"),
    )
    settings = make_settings()

    result = asyncio.run(check_minio(settings, PreflightOptions()))

    assert result.status == "ok"
    assert "bucket 'yasmina'" in result.detail


def test_clamav_ping_against_local_fake_daemon() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(16)
        writer.write(b"PONG\0")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def scenario() -> CheckResult:
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        try:
            settings = make_settings(clamav_host="127.0.0.1", clamav_port=port)
            return await check_clamav(settings, PreflightOptions(timeout=2.0))
        finally:
            server.close()
            await server.wait_closed()

    result = asyncio.run(scenario())

    assert result.status == "ok"
    assert "PONG" in result.detail
