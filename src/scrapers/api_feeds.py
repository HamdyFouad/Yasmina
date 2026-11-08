"""Open-data feed scrapers and the API-first portal preference.

TED, XVergabe and many state portals expose JSON/XML feeds. When a target has an
``api`` block, the worker builds one of these adapters first and falls back to
the HTML engine only when the feed fails and ``fallback_to_html`` is enabled.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, ClassVar, cast
from urllib.parse import urljoin

from defusedxml import ElementTree as SafeET

from src.config import Settings
from src.models.enums import ProcurementProcedure
from src.scrapers.base_scraper import BaseScraper, ObjectWriter, ScrapeReport
from src.scrapers.domain_limits import DomainRateLimiter
from src.scrapers.models import (
    DiscoveredTender,
    extract_cpv_codes,
    filter_hygiene_cpv,
    parse_portal_date,
    parse_portal_datetime,
)
from src.scrapers.portal_config import PortalApiConfig, PortalTarget

__all__ = [
    "ApiFeedScraper",
    "TEDApiScraper",
    "XVergabeApiScraper",
    "build_api_scraper",
]

_DEFAULT_MAPPING = {
    "items": "notices",
    "id": "id",
    "title": "title",
    "client": "client",
    "deadline": "deadline",
    "cpv": "cpv_codes",
    "url": "url",
    "publication_date": "publication_date",
}

_TED_MAPPING = {
    "items": "notices",
    "id": "publication-number",
    "title": "notice-title",
    "client": "buyer-name",
    "deadline": "deadline-date-lot",
    "cpv": "classification-cpv",
    "url": "notice-url",
    "publication_date": "publication-date",
}

_XVERGABE_MAPPING = {
    "items": "notice",
    "id": "id",
    "title": "title",
    "client": "client",
    "deadline": "deadline",
    "cpv": "cpv",
    "url": "url",
    "publication_date": "publication_date",
}


def _lookup(payload: Any, path: str | None) -> Any:
    if not path:
        return None
    current = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        for key in ("eng", "de", "en", "text", "value", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            text = _text(candidate)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(part for part in (_text(item) for item in value) if part)
    return str(value).strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _xml_to_dict(element: ET.Element) -> dict[str, Any]:
    children = list(element)
    if not children:
        return {"text": (element.text or "").strip()}
    result: dict[str, Any] = {}
    for child in children:
        tag = child.tag.rsplit("}", 1)[-1]
        if list(child):
            result[tag] = _xml_to_dict(child)
        else:
            result[tag] = (child.text or "").strip()
    return result


def _xml_items(text: str, item_tag: str) -> list[Mapping[str, Any]]:
    root = SafeET.fromstring(text)
    wanted = item_tag.rsplit("}", 1)[-1].lower()
    items: list[Mapping[str, Any]] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag == wanted:
            items.append(_xml_to_dict(element))
    return items


class ApiFeedScraper(BaseScraper):
    """Parse a configured open JSON/XML feed into ``DiscoveredTender`` rows."""

    default_mapping: ClassVar[Mapping[str, str]] = _DEFAULT_MAPPING
    default_detail_template: ClassVar[str | None] = None
    start_url: str = ""

    def __init__(
        self,
        settings: Settings,
        storage: ObjectWriter | None = None,
        *,
        api_config: PortalApiConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, storage, **kwargs)
        self.api_config = api_config

    @property
    def mapping(self) -> Mapping[str, str]:
        return {**self.default_mapping, **self.api_config.mapping}

    def _detail_url(self, payload: Mapping[str, Any], portal_id: str) -> str:
        mapping = self.mapping
        raw = _text(_lookup(payload, mapping.get("url")))
        if not raw:
            template = self.api_config.detail_url_template or self.default_detail_template
            if template:
                raw = template.format(id=portal_id)
        if not raw:
            raw = f"{self.start_url.rstrip('/')}/{portal_id}"
        return str(urljoin(self.start_url, raw))

    def _parse_deadline(self, payload: Mapping[str, Any]) -> datetime | None:
        raw = _text(_lookup(payload, self.mapping.get("deadline")))
        return parse_portal_datetime(raw) if raw else None

    def _parse_publication(self, payload: Mapping[str, Any]) -> date | None:
        raw = _text(_lookup(payload, self.mapping.get("publication_date")))
        return parse_portal_date(raw) if raw else None

    def _cpv_codes(self, payload: Mapping[str, Any]) -> tuple[str, ...]:
        raw = _lookup(payload, self.mapping.get("cpv"))
        values: list[str] = []
        for item in _as_list(raw):
            values.extend(extract_cpv_codes(_text(item)))
        return filter_hygiene_cpv(tuple(values))

    def _items(self, payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        raw_items = _lookup(payload, self.mapping.get("items"))
        if not isinstance(raw_items, list):
            return []
        return [item for item in raw_items if isinstance(item, Mapping)]

    async def discover(
        self,
        *,
        max_pages: int | None = None,
        since: datetime | None = None,
    ) -> list[DiscoveredTender]:
        page = await self.fetch(self.api_config.url)
        if self.api_config.format == "json":
            payload = json.loads(page.text)
        else:
            item_tag = self.mapping.get("items", "notice")
            payload = _xml_items(page.text, item_tag)
            if isinstance(payload, list):
                payload = {self.mapping.get("items", "notice"): payload}

        since_date = since.date() if since is not None else None
        discovered: list[DiscoveredTender] = []
        skipped_cpv = 0
        skipped_invalid = 0
        errors: list[str] = []
        for item in self._items(payload):
            portal_id = (
                _text(_lookup(item, self.mapping.get("id")))
                or hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode("utf-8")).hexdigest()[
                    :16
                ]
            )
            title = _text(_lookup(item, self.mapping.get("title")))
            client = _text(_lookup(item, self.mapping.get("client"))) or "Unbekannter Auftraggeber"
            deadline = self._parse_deadline(item)
            cpv_codes = self._cpv_codes(item)
            publication_date = self._parse_publication(item)
            if since_date is not None and publication_date is not None and publication_date < since_date:
                continue
            if not cpv_codes:
                skipped_cpv += 1
                continue
            if not title or deadline is None:
                errors.append(f"{portal_id}: missing title or deadline")
                skipped_invalid += 1
                continue
            try:
                discovered.append(
                    DiscoveredTender(
                        portal=self.portal_name,
                        portal_code=self.portal_code,
                        portal_id=portal_id[:64],
                        title=title[:512],
                        client_name=client[:256],
                        detail_url=self._detail_url(item, portal_id),
                        submission_deadline=deadline.astimezone(UTC),
                        cpv_codes=cpv_codes,
                        publication_date=publication_date,
                        procedure=ProcurementProcedure.OPEN_PROCEDURE,
                        raw_object=page.archived_object,
                        metadata={"listing": self.api_config.type, "api_url": self.api_config.url},
                    )
                )
            except Exception as exc:
                errors.append(f"{portal_id}: {exc}")
                skipped_invalid += 1

        self.last_report = ScrapeReport(
            portal=self.portal_name,
            discovered=tuple(discovered),
            pages_fetched=1,
            skipped_cpv=skipped_cpv,
            skipped_invalid=skipped_invalid,
            errors=tuple(errors),
        )
        return discovered


class TEDApiScraper(ApiFeedScraper):
    """TED/open notice feed adapter (JSON feed or API export URL)."""

    default_mapping: ClassVar[Mapping[str, str]] = _TED_MAPPING
    default_detail_template = "https://ted.europa.eu/en/notice/-/detail/{id}"


class XVergabeApiScraper(ApiFeedScraper):
    """XVergabe/DTVP XML feed adapter."""

    default_mapping: ClassVar[Mapping[str, str]] = _XVERGABE_MAPPING


_API_TYPES: dict[str, type[ApiFeedScraper]] = {
    "ted": TEDApiScraper,
    "xvergabe": XVergabeApiScraper,
    "dtvp": XVergabeApiScraper,
    "feed": ApiFeedScraper,
    "json": ApiFeedScraper,
    "xml": ApiFeedScraper,
}


def build_api_scraper(
    target: PortalTarget,
    settings: Settings,
    storage: ObjectWriter | None = None,
    *,
    rate_limiter: DomainRateLimiter | None = None,
    **scraper_kwargs: Any,
) -> ApiFeedScraper:
    if target.api is None:
        raise ValueError(f"portal {target.id!r} has no API configuration")
    base = _API_TYPES.get(target.api.type.lower(), ApiFeedScraper)
    portal_cls = type(
        f"{target.id.title().replace('-', '')}ApiScraper",
        (base,),
        {
            "portal_name": target.portal_name,
            "portal_code": target.portal_code,
            "start_url": target.start_url,
        },
    )
    return cast(
        ApiFeedScraper,
        portal_cls(
            settings,
            storage,
            api_config=target.api,
            rate_limiter=rate_limiter,
            request_delay_seconds=target.request_delay_seconds,
            **scraper_kwargs,
        ),
    )
