"""Config-driven catalogue of procurement portals.

Portals are described in ``config/portals.yaml`` so that adding a new source is a
data change instead of a new Python class. Each entry selects an *engine* (the
underlying portal software family) and optionally an API feed that must be
preferred over HTML scraping.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

__all__ = [
    "PortalApiConfig",
    "PortalConfigError",
    "PortalTarget",
    "default_portal_config_path",
    "load_portal_targets",
    "parse_portal_targets",
]


class PortalConfigError(ValueError):
    """Raised when the portal configuration file is invalid."""


@dataclass(frozen=True, slots=True)
class PortalApiConfig:
    """Optional open-data API/feed that should be preferred over HTML."""

    type: str
    url: str
    format: Literal["json", "xml"] = "json"
    fallback_to_html: bool = True
    mapping: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, Any] = field(default_factory=dict)
    detail_url_template: str | None = None


@dataclass(frozen=True, slots=True)
class PortalTarget:
    """One configured procurement portal."""

    id: str
    name: str
    start_url: str
    engine: str
    portal_name: str
    portal_code: str
    country: str = "DE"
    region: str | None = None
    domain: str | None = None
    enabled: bool = True
    request_delay_seconds: float | None = None
    max_pages: int | None = None
    api: PortalApiConfig | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def effective_domain(self) -> str:
        """Domain used for robots/rate-limit/lock keys."""

        return self.domain or urlsplit(self.start_url).netloc.lower()


def default_portal_config_path() -> Path:
    """Repository-relative default, with a CWD fallback for wheel installs."""

    packaged = Path(__file__).resolve().parents[2] / "config" / "portals.yaml"
    if packaged.is_file():
        return packaged
    working = Path.cwd() / "config" / "portals.yaml"
    return working if working.is_file() else packaged


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PortalConfigError(f"{context} must be a mapping")
    return value


def _require_text(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortalConfigError(f"{context} must be a non-empty string")
    return value.strip()


def _parse_api(raw: Any, *, context: str) -> PortalApiConfig | None:
    if raw is None:
        return None
    data = _require_mapping(raw, context=context)
    url = _require_text(data.get("url"), context=f"{context}.url")
    if not url.startswith(("http://", "https://")):
        raise PortalConfigError(f"{context}.url must be an absolute HTTP(S) URL")
    fmt = str(data.get("format", "json")).lower()
    if fmt not in {"json", "xml"}:
        raise PortalConfigError(f"{context}.format must be 'json' or 'xml'")
    mapping = data.get("mapping") or {}
    headers = data.get("headers") or {}
    query = data.get("query") or {}
    if not isinstance(mapping, Mapping) or not isinstance(headers, Mapping) or not isinstance(query, Mapping):
        raise PortalConfigError(f"{context}.mapping/headers/query must be mappings")
    return PortalApiConfig(
        type=str(data.get("type", "feed")).strip() or "feed",
        url=url,
        format="json" if fmt == "json" else "xml",
        fallback_to_html=bool(data.get("fallback_to_html", True)),
        mapping={str(key): str(value) for key, value in mapping.items()},
        headers={str(key): str(value) for key, value in headers.items()},
        query=dict(query),
        detail_url_template=(
            str(data["detail_url_template"]).strip() if data.get("detail_url_template") else None
        ),
    )


def _parse_target(raw: Any, *, index: int) -> PortalTarget:
    data = _require_mapping(raw, context=f"portals[{index}]")
    portal_id = _require_text(data.get("id"), context=f"portals[{index}].id")
    start_url = _require_text(data.get("start_url"), context=f"{portal_id}.start_url")
    if not start_url.startswith(("http://", "https://")):
        raise PortalConfigError(f"{portal_id}.start_url must be an absolute HTTP(S) URL")
    domain = data.get("domain")
    if domain is not None:
        domain = _require_text(domain, context=f"{portal_id}.domain").lower()
    delay = data.get("request_delay_seconds")
    if delay is not None and (not isinstance(delay, (int, float)) or delay < 0):
        raise PortalConfigError(f"{portal_id}.request_delay_seconds must be >= 0")
    max_pages = data.get("max_pages")
    if max_pages is not None and (not isinstance(max_pages, int) or max_pages < 1):
        raise PortalConfigError(f"{portal_id}.max_pages must be a positive integer")
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise PortalConfigError(f"{portal_id}.metadata must be a mapping")
    return PortalTarget(
        id=portal_id,
        name=_require_text(data.get("name"), context=f"{portal_id}.name"),
        start_url=start_url,
        engine=_require_text(data.get("engine"), context=f"{portal_id}.engine").lower(),
        portal_name=str(data.get("portal_name", portal_id)).strip() or portal_id,
        portal_code=str(data.get("portal_code", portal_id[:8])).strip().upper() or portal_id[:8].upper(),
        country=str(data.get("country", "DE")).strip().upper() or "DE",
        region=str(data["region"]).strip() if data.get("region") else None,
        domain=domain,
        enabled=bool(data.get("enabled", True)),
        request_delay_seconds=float(delay) if delay is not None else None,
        max_pages=max_pages,
        api=_parse_api(data.get("api"), context=f"{portal_id}.api"),
        metadata=dict(metadata),
    )


def parse_portal_targets(payload: Any, *, enabled_only: bool = True) -> list[PortalTarget]:
    """Validate a parsed YAML/JSON payload and return portal targets."""

    if isinstance(payload, Mapping):
        raw_targets = payload.get("portals")
        defaults = payload.get("defaults") or {}
        if not isinstance(defaults, Mapping):
            raise PortalConfigError("portal configuration 'defaults' must be a mapping")
    else:
        raw_targets = payload
        defaults = {}
    if not isinstance(raw_targets, Iterable) or isinstance(raw_targets, (str, bytes, Mapping)):
        raise PortalConfigError("portal configuration must contain a 'portals' list")

    targets: list[PortalTarget] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, Mapping):
            raise PortalConfigError(f"portals[{index}] must be a mapping")
        target = _parse_target({**defaults, **raw}, index=index)
        if target.id in seen:
            raise PortalConfigError(f"duplicate portal id {target.id!r}")
        seen.add(target.id)
        targets.append(target)
    if not targets:
        raise PortalConfigError("portal configuration is empty")
    if enabled_only:
        targets = [target for target in targets if target.enabled]
        if not targets:
            raise PortalConfigError("no enabled portals configured")
    return targets


def load_portal_targets(
    path: str | Path | None = None,
    *,
    enabled_only: bool = True,
) -> list[PortalTarget]:
    """Load and validate ``portals.yaml``."""

    config_path = Path(path) if path is not None else default_portal_config_path()
    if not config_path.is_absolute():
        config_path = default_portal_config_path().parent.parent / config_path
    if not config_path.is_file():
        raise PortalConfigError(f"portal configuration not found: {config_path}")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PortalConfigError(f"cannot parse portal configuration {config_path}: {exc}") from exc
    return parse_portal_targets(payload, enabled_only=enabled_only)
