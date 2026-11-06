"""Startup preflight checks for Project Yasmina.

Run this before starting the API, workers or LangGraph jobs::

    python -m src.preflight
    python -m src.preflight --json
    python -m src.preflight --skip n8n --skip clamav

The check is read-only except for a short write probe in the GAEB export
directory. It exits non-zero when a required dependency is broken.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.api.security import SecurityConfigurationError, validate_security_configuration
from src.config import Settings, ensure_env_file, get_settings, reset_settings_cache

__all__ = [
    "CheckResult",
    "PreflightOptions",
    "PreflightReport",
    "check_clamav",
    "check_configuration",
    "check_filesystem",
    "check_minio",
    "check_n8n",
    "check_postgres",
    "main",
    "parse_args",
    "run_preflight",
]

CheckStatus = Literal["ok", "warn", "fail", "skipped"]

STATUS_LABELS: dict[CheckStatus, str] = {
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
    "skipped": "SKIP",
}

SKIPPABLE_SERVICES = ("postgres", "minio", "clamav", "n8n")


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of one preflight check."""

    name: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Aggregated preflight results for console/JSON output."""

    environment: str
    checks: tuple[CheckResult, ...]

    @property
    def failed(self) -> bool:
        return any(check.status == "fail" for check in self.checks)

    @property
    def warnings(self) -> int:
        return sum(1 for check in self.checks if check.status == "warn")

    def to_dict(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "ok": not self.failed,
            "warnings": self.warnings,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class PreflightOptions:
    """Runtime options for :func:`run_preflight`."""

    timeout: float = 5.0
    skip: frozenset[str] = frozenset()
    require_n8n: bool = False


# ---------------------------------------------------------------------------
# Configuration & filesystem
# ---------------------------------------------------------------------------


def check_configuration(settings: Settings) -> list[CheckResult]:
    """Validate hard production guardrails and report missing optional services."""

    problems: list[str] = []
    warnings: list[str] = []

    try:
        validate_security_configuration(settings)
    except SecurityConfigurationError as exc:
        problems.append(str(exc))

    if settings.app_env == "production":
        if not settings.ed25519_approval_public_key:
            problems.append("ED25519_APPROVAL_PUBLIC_KEY is required in production (HITL)")
        if not settings.approval_signature_required:
            problems.append("APPROVAL_SIGNATURE_REQUIRED must remain true in production")
        if not settings.deepseek_api_key:
            warnings.append("DEEPSEEK_API_KEY is not set; LLM extraction will be disabled")
        if not settings.erpnext_api_key or not settings.erpnext_api_secret:
            warnings.append("ERPNext credentials are not set; ERPNext export will be disabled")
    else:
        if not settings.deepseek_api_key:
            warnings.append("DEEPSEEK_API_KEY is not set (LLM extraction disabled)")
        if not settings.ed25519_approval_public_key:
            warnings.append("ED25519_APPROVAL_PUBLIC_KEY is not set (approval verification disabled)")

    results: list[CheckResult] = []
    if problems:
        results.append(CheckResult("configuration", "fail", "; ".join(problems)))
    else:
        results.append(CheckResult("configuration", "ok", f"environment={settings.app_env}"))
    if warnings:
        results.append(CheckResult("configuration.optional", "warn", "; ".join(warnings)))
    return results


def check_filesystem(settings: Settings) -> CheckResult:
    """Ensure the export directory exists and is writable."""

    directory = settings.gaeb_export_dir
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".preflight-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult("filesystem", "fail", f"{directory}: {exc}")
    return CheckResult("filesystem", "ok", f"{directory} is writable")


# ---------------------------------------------------------------------------
# HTTP / TCP probes
# ---------------------------------------------------------------------------


def _http_probe(url: str, timeout: float) -> tuple[bool, str]:
    # Internal services must never be routed through a corporate HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            status = getattr(response, "status", 0)
            body = response.read(256).decode("utf-8", "replace").strip()
            if 200 <= status < 300:
                return True, f"HTTP {status}" + (f" {body}" if body else "")
            return False, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, str(getattr(exc, "reason", exc))


def _minio_bucket_check(settings: Settings) -> tuple[bool, str]:
    """Authenticate against MinIO and verify the configured bucket is reachable."""

    try:
        from minio import Minio
        from minio.error import S3Error
    except ImportError as exc:  # pragma: no cover - dependency is mandatory
        return False, f"minio client is not installed: {exc}"

    endpoint = settings.minio_endpoint.strip()
    secure = settings.minio_secure or endpoint.startswith("https://")
    endpoint = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")

    client = Minio(
        endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=secure,
    )
    try:
        exists = client.bucket_exists(settings.minio_bucket)
    except S3Error as exc:
        return False, f"S3 {exc.code}: {exc.message}"
    except Exception as exc:
        return False, str(exc)

    if not exists:
        return False, f"bucket {settings.minio_bucket!r} does not exist or is not accessible"
    return True, f"authenticated; bucket {settings.minio_bucket!r} accessible"


async def check_minio(settings: Settings, options: PreflightOptions) -> CheckResult:
    if "minio" in options.skip:
        return CheckResult("minio", "skipped", "skipped via --skip minio")

    endpoint = settings.minio_endpoint.strip().rstrip("/")
    if "://" not in endpoint:
        scheme = "https" if settings.minio_secure else "http"
        endpoint = f"{scheme}://{endpoint}"
    healthy, health_detail = await asyncio.to_thread(
        _http_probe, f"{endpoint}/minio/health/live", options.timeout
    )
    if not healthy:
        return CheckResult("minio", "fail", f"{settings.minio_endpoint}: {health_detail}")

    authenticated, auth_detail = await asyncio.to_thread(_minio_bucket_check, settings)
    if not authenticated:
        return CheckResult("minio", "fail", f"{settings.minio_endpoint}: {auth_detail}")

    return CheckResult("minio", "ok", f"{settings.minio_endpoint} {auth_detail}")


async def check_n8n(settings: Settings, options: PreflightOptions) -> CheckResult:
    if "n8n" in options.skip:
        return CheckResult("n8n", "skipped", "skipped via --skip n8n")

    url = f"{settings.n8n_webhook_base_url.rstrip('/')}/healthz"
    ok, detail = await asyncio.to_thread(_http_probe, url, options.timeout)
    if ok:
        return CheckResult("n8n", "ok", f"{settings.n8n_webhook_base_url} reachable")
    status: CheckStatus = "fail" if options.require_n8n else "warn"
    suffix = "required by --require-n8n" if options.require_n8n else "optional automation profile"
    return CheckResult("n8n", status, f"{settings.n8n_webhook_base_url}: {detail} ({suffix})")


async def check_clamav(settings: Settings, options: PreflightOptions) -> CheckResult:
    if "clamav" in options.skip:
        return CheckResult("clamav", "skipped", "skipped via --skip clamav")

    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port),
            timeout=options.timeout,
        )
        writer.write(b"zPING\0")
        await asyncio.wait_for(writer.drain(), timeout=options.timeout)
        response = await asyncio.wait_for(reader.read(32), timeout=options.timeout)
    except (OSError, TimeoutError) as exc:
        return CheckResult(
            "clamav",
            "fail",
            f"{settings.clamav_host}:{settings.clamav_port} unreachable: {exc}",
        )
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):  # pragma: no cover - best-effort cleanup
                pass

    if b"PONG" in response:
        return CheckResult("clamav", "ok", f"clamd responded PONG on {settings.clamav_host}:{settings.clamav_port}")
    return CheckResult("clamav", "fail", f"unexpected clamd response: {response[:32]!r}")


# ---------------------------------------------------------------------------
# PostgreSQL + schema
# ---------------------------------------------------------------------------


def _alembic_head() -> str | None:
    ini = Path("alembic.ini")
    if not ini.exists():
        return None
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        return ScriptDirectory.from_config(AlembicConfig(str(ini))).get_current_head()
    except Exception:  # pragma: no cover - configuration errors are reported by alembic itself
        return None


async def check_postgres(settings: Settings, options: PreflightOptions) -> list[CheckResult]:
    if "postgres" in options.skip:
        return [
            CheckResult("postgres", "skipped", "skipped via --skip postgres"),
            CheckResult("postgres.schema", "skipped", "skipped via --skip postgres"),
        ]

    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - dependency is mandatory
        return [
            CheckResult("postgres", "fail", f"asyncpg is not installed: {exc}"),
            CheckResult("postgres.schema", "skipped", "asyncpg unavailable"),
        ]

    try:
        connection = await asyncpg.connect(settings.asyncpg_dsn, timeout=options.timeout)
    except Exception as exc:
        return [
            CheckResult("postgres", "fail", f"cannot connect: {exc}"),
            CheckResult("postgres.schema", "skipped", "connection failed"),
        ]

    version = "unknown"
    try:
        version = await connection.fetchval("SHOW server_version")
        extension = await connection.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
        revision: str | None = None
        if await connection.fetchval("SELECT to_regclass('public.alembic_version')") is not None:
            revision = await connection.fetchval("SELECT version_num FROM alembic_version")
        trigger_rows = await connection.fetch(
            "SELECT tgname FROM pg_trigger WHERE tgname IN ('trg_audit_logs_append_only', 'trg_audit_logs_no_truncate')"
        )
        trigger_names = {row["tgname"] for row in trigger_rows}
        index = await connection.fetchval(
            "SELECT indexname FROM pg_indexes WHERE indexname = 'ix_catalog_articles_embedding_hnsw'"
        )
    except Exception as exc:
        return [
            CheckResult("postgres", "ok", f"PostgreSQL {version}"),
            CheckResult("postgres.schema", "fail", f"schema inspection failed: {exc}"),
        ]
    finally:
        await connection.close()

    head = _alembic_head()
    issues: list[str] = []
    notes: list[str] = []

    if extension is None:
        issues.append("pgvector extension not installed")
    if revision is None:
        issues.append("database migrations not applied (run alembic upgrade head)")
    elif head is None:
        notes.append("alembic head unknown")
    elif revision != head:
        issues.append(f"database revision {revision} != head {head}")
    if trigger_names != {"trg_audit_logs_append_only", "trg_audit_logs_no_truncate"}:
        issues.append("audit append-only triggers missing (UPDATE/DELETE/TRUNCATE)")
    if index is None:
        issues.append("HNSW embedding index missing")

    if issues:
        schema_detail = "; ".join(issues)
        schema_status: CheckStatus = "fail"
    else:
        schema_detail = f"vector={extension}, alembic={revision}, audit triggers and HNSW index present"
        if notes:
            schema_detail += f" ({'; '.join(notes)})"
        schema_status = "ok"

    return [
        CheckResult("postgres", "ok", f"PostgreSQL {version}"),
        CheckResult("postgres.schema", schema_status, schema_detail),
    ]


# ---------------------------------------------------------------------------
# Orchestration & CLI
# ---------------------------------------------------------------------------


async def run_preflight(settings: Settings, options: PreflightOptions) -> list[CheckResult]:
    """Run all checks sequentially and return their results."""

    results = check_configuration(settings)
    results.append(check_filesystem(settings))
    results.extend(await check_postgres(settings, options))
    results.append(await check_minio(settings, options))
    results.append(await check_clamav(settings, options))
    results.append(await check_n8n(settings, options))
    return results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yasmina-preflight",
        description="Validate Project Yasmina configuration and external services.",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-check timeout in seconds (default: PREFLIGHT_TIMEOUT_SECONDS)",
    )
    parser.add_argument(
        "--skip",
        action="append",
        choices=SKIPPABLE_SERVICES,
        metavar="SERVICE",
        help="skip a service check (repeatable)",
    )
    parser.add_argument(
        "--require-n8n",
        action="store_true",
        help="treat an unreachable n8n instance as a failure",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if ensure_env_file():
        reset_settings_cache()
        print("Created .env from .env.example", file=sys.stderr)

    try:
        settings = get_settings()
    except Exception as exc:
        result = CheckResult("configuration", "fail", str(exc))
        if args.json:
            print(json.dumps(PreflightReport("unknown", (result,)).to_dict(), indent=2))
        else:
            print(f"[FAIL] configuration: {exc}")
        return 1

    timeout = args.timeout if args.timeout is not None else settings.preflight_timeout_seconds
    if timeout <= 0:
        print("--timeout must be greater than zero", file=sys.stderr)
        return 2

    options = PreflightOptions(
        timeout=timeout,
        skip=frozenset(args.skip or ()),
        require_n8n=args.require_n8n,
    )
    checks = asyncio.run(run_preflight(settings, options))
    report = PreflightReport(settings.app_env, tuple(checks))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Project Yasmina preflight (env={settings.app_env})")
        for check in report.checks:
            print(f"[{STATUS_LABELS[check.status]}] {check.name}: {check.detail}")
        print("Result:", "FAILED" if report.failed else "OK")

    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
