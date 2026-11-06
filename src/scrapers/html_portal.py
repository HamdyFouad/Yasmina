"""Generic HTML listing/detail parser shared by the German portal scrapers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from src.models.enums import ProcurementProcedure
from src.scrapers.base_scraper import BaseScraper, ScrapedArtifact, ScrapedPage, ScrapeReport
from src.scrapers.models import (
    DiscoveredTender,
    PortalAttachment,
    extract_cpv_codes,
    filter_hygiene_cpv,
    parse_portal_date,
    parse_portal_datetime,
)

__all__ = ["HTMLPortalScraper"]

_CLEAN_RE = re.compile(r"\s+")
_CPV_LABEL_RE = re.compile(r"CPV(?:[- ]Codes?)?\s*:?\s*(?P<value>[^|;]{0,200})", re.IGNORECASE)
_CLIENT_LABEL_RE = re.compile(
    r"(?:Auftraggeber(?:in)?|Vergabestelle|Public body|Contracting authority)\s*[:\-]\s*"
    r"(?P<value>[^|;]{0,200})",
    re.IGNORECASE,
)
_DEADLINE_LABEL_RE = re.compile(
    r"(?:Abgabe(?:frist)?|Angebots(?:frist|abgabe)?|Teilnahme(?:frist)?|Bewerbungsfrist|"
    r"Frist|Submission deadline|Offer deadline|Deadline|Expiration time|End of application period)"
    r"\s*[:\-]?\s*(?P<value>[^|;]{0,160})",
    re.IGNORECASE,
)
_PUBLICATION_LABEL_RE = re.compile(
    r"(?:Veröffentlicht(?: am)?|Bekanntmachung(?:sdatum)?|Publication date|Published)"
    r"\s*[:\-]?\s*(?P<value>[^|;]{0,160})",
    re.IGNORECASE,
)
_PROCEDURE_RE = re.compile(
    r"(?P<value>offenes Verfahren|nicht offenes Verfahren|beschränkte Ausschreibung|"
    r"Verhandlungsverfahren|wettbewerblicher Dialog|Direktauftrag|Direktvergabe|"
    r"VOB/A|UVgO|VgV|SektVO|KonzVgV)",
    re.IGNORECASE,
)
_GENERIC_TITLES = {
    "auftragsbekanntmachung",
    "bekanntmachung",
    "details",
    "ausschreibung",
    "tender",
    "notice",
}
_CARD_SELECTORS: tuple[str, ...] = (
    "li[id^='tender-']",
    "article.tender",
    "article",
    ".tender-card",
    ".tender",
    ".search-result",
    ".result",
    ".bekanntmachung",
    ".list-item",
    "tr.tender",
    "table tbody tr",
)
_DETAIL_HINTS = (
    "/publicationsframe/",
    "/bekanntmachung",
    "/ausschreibung",
    "/notice",
    "/notices/",
    "/tender/details",
    "/details",
    "/detail/",
    "/publication/",
    "/vergabe",
)
_DETAIL_REJECTS = ("publisher", "apply", "mailto:", "javascript:", "#")
_ATTACHMENT_SUFFIXES = (".pdf", ".x83", ".d83", ".x84", ".zip", ".xml")
_ATTACHMENT_REJECTS = ("mailto:", "javascript:", "#", "apply")


@dataclass(frozen=True, slots=True)
class _AttachmentLink:
    url: str
    filename: str


def _clean(value: str | None) -> str:
    return _CLEAN_RE.sub(" ", value or "").strip()


def _text(element: Tag | None) -> str:
    return _clean(element.get_text(" ", strip=True)) if element is not None else ""


def _label_value(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    value = _clean(match.group("value"))
    return value or None


def _labeled_value(element: Tag | None, pattern: re.Pattern[str]) -> str | None:
    """Read ``Label: value`` from a block, or ``Label`` followed by its value."""

    if element is None:
        return None
    lines = [_clean(line) for line in element.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if match is None:
            continue
        value = _clean(match.group("value"))
        if value:
            return value
        if index + 1 < len(lines):
            return lines[index + 1]
    return None


def _first_attr(element: Tag, *names: str) -> str | None:
    for name in names:
        value = element.get(name)
        if value:
            return _clean(str(value))
    return None


def _procedure(value: str) -> ProcurementProcedure:
    lowered = value.casefold()
    if "wettbewerblich" in lowered or "competitive dialogue" in lowered:
        return ProcurementProcedure.COMPETITIVE_DIALOGUE
    if "verhandlung" in lowered or "negotiated" in lowered:
        return ProcurementProcedure.NEGOTIATED_PROCEDURE
    if "nicht offen" in lowered or "beschränk" in lowered or "restricted" in lowered:
        return ProcurementProcedure.RESTRICTED_PROCEDURE
    if "direkt" in lowered or "direct award" in lowered:
        return ProcurementProcedure.DIRECT_AWARD
    return ProcurementProcedure.OPEN_PROCEDURE


def _published_date(text: str, element: Tag | None = None) -> date | None:
    value = _labeled_value(element, _PUBLICATION_LABEL_RE) or _label_value(_PUBLICATION_LABEL_RE, text)
    if value:
        parsed = parse_portal_date(value)
        if parsed is not None:
            return parsed
    if element is not None:
        date_node = element.select_one(".item-right .date, .publication-date, .published-date")
        month_node = element.select_one(".item-right .month")
        if date_node is not None and month_node is not None:
            parsed = parse_portal_date(f"{_text(date_node)} {_text(month_node)}")
            if parsed is not None:
                return parsed
    return None


def _deadline(text: str, element: Tag | None = None) -> datetime | None:
    value = _labeled_value(element, _DEADLINE_LABEL_RE) or _label_value(_DEADLINE_LABEL_RE, text)
    if value:
        parsed = parse_portal_datetime(value)
        if parsed is not None:
            return parsed
    for selector in (".deadline", ".frist", ".offer-deadline", ".expiration-time"):
        if element is not None:
            node = element.select_one(selector)
            if node is not None:
                parsed = parse_portal_datetime(_text(node))
                if parsed is not None:
                    return parsed
    return None


def _clean_client(value: str) -> str:
    for marker in (
        "Show further assignments by",
        "Weitere Vergaben der Vergabestelle",
        "Details",
        "Apply",
        "Recommend",
    ):
        value = value.split(marker, 1)[0]
    return _clean(value)[:256]


def _client(text: str, element: Tag | None = None) -> str | None:
    value = _labeled_value(element, _CLIENT_LABEL_RE) or _label_value(_CLIENT_LABEL_RE, text)
    if value:
        return _clean_client(value)
    if element is not None:
        for selector in (".client", ".auftraggeber", ".authority", ".contracting-authority", ".text-muted"):
            node = element.select_one(selector)
            if node is None:
                continue
            value = _clean(str(node.get("title") or "") or _text(node))
            if value:
                return _clean_client(value)
        value = _first_attr(element, "data-client", "data-authority", "data-auftraggeber")
        if value:
            return _clean_client(value)
    return None


def _title(element: Tag, link: Tag, text: str) -> str:
    for selector in ("h1", "h2", "h3", "h4", "h5", "strong", ".title", ".headline"):
        node = element.select_one(selector)
        candidate = _clean(node.get_text(" ", strip=True)) if node is not None else ""
        if candidate and candidate.casefold() not in _GENERIC_TITLES:
            return candidate[:512]
    candidate = _clean(link.get_text(" ", strip=True))
    if candidate and candidate.casefold() not in _GENERIC_TITLES:
        return candidate[:512]
    candidate = text[:512]
    return candidate or "Unbenannte Ausschreibung"


def _cpvs(element: Tag, text: str) -> tuple[str, ...]:
    values: list[str] = []
    for attr in ("data-cpv", "data-cpvs", "data-cpv-codes", "data-classification"):
        value = element.get(attr)
        if value:
            values.extend(extract_cpv_codes(str(value)))
    values.extend(extract_cpv_codes(text))
    labelled = _label_value(_CPV_LABEL_RE, text)
    if labelled:
        values.extend(extract_cpv_codes(labelled))
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _detail_link(element: Tag, page_url: str) -> str | None:
    candidates: list[str] = []
    for anchor in element.select("a[href]"):
        href = str(anchor.get("href", "")).strip()
        lowered = href.casefold()
        if not href or lowered.startswith(("#", "mailto:", "javascript:")):
            continue
        if any(reject in lowered for reject in _DETAIL_REJECTS):
            continue
        candidates.append(href)
    if not candidates:
        return None
    preferred = next(
        (href for href in candidates if any(hint in href.casefold() for hint in _DETAIL_HINTS)),
        candidates[0],
    )
    return urljoin(page_url, preferred)


def _portal_id(element: Tag, url: str) -> str:
    element_id = element.get("id")
    if element_id and str(element_id).startswith("tender-"):
        return str(element_id).removeprefix("tender-")[:64]
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    for key in ("id", "noticeId", "noticeIdentifier", "bekanntmachungId", "projectId"):
        if query.get(key):
            return query[key][0][:64]
    tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if tail:
        return tail.removesuffix(".html")[:64]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _rel_next(value: Any) -> bool:
    return value is not None and "next" in value


def _next_page(soup: BeautifulSoup, page_url: str) -> str | None:
    rel_next = soup.find("a", attrs={"rel": _rel_next})
    candidates: list[Tag] = [rel_next] if isinstance(rel_next, Tag) else []
    for anchor in soup.select("a[href]"):
        text = _clean(anchor.get_text(" ", strip=True)).casefold()
        aria = _clean(str(anchor.get("aria-label", ""))).casefold()
        title = _clean(str(anchor.get("title", ""))).casefold()
        if (
            text in {"weiter", "nächste", "nächste seite", "weiter »", "»"}
            or "nächste seite" in aria
            or "nächste seite" in title
            or "next page" in aria
            or "next page" in title
        ):
            candidates.append(anchor)
    for anchor in candidates:
        href = str(anchor.get("href", "")).strip()
        if href and not href.startswith(("#", "javascript:", "mailto:")):
            return urljoin(page_url, href)
    return None


@dataclass(frozen=True, slots=True)
class _ParsedCard:
    url: str
    title: str
    client: str | None
    deadline: datetime | None
    cpv_codes: tuple[str, ...]
    publication_date: date | None
    procedure: ProcurementProcedure
    portal_id: str
    text: str
    raw_object: str | None


class HTMLPortalScraper(BaseScraper):
    """Parse common listing cards and enrich incomplete cards from detail pages."""

    start_url: str = ""
    follow_iframe: ClassVar[bool] = False
    fetch_details: bool = True

    def __init__(
        self, *args: Any, start_url: str | None = None, fetch_details: bool | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        if start_url is not None:
            self.start_url = start_url
        if fetch_details is not None:
            self.fetch_details = fetch_details

    async def _first_listing_page(self, url: str) -> ScrapedPage:
        page = await self.fetch(url)
        if not self.follow_iframe:
            return page
        soup = BeautifulSoup(page.text, "html.parser")
        if self._select_cards(soup):
            return page
        iframes = soup.select("iframe[src]")
        if not iframes:
            return page
        iframe = next(
            (
                node
                for node in iframes
                if any(
                    token in str(node.get("src", "")).casefold()
                    for token in ("publication", "tender", "vergabe")
                )
            ),
            iframes[0],
        )
        iframe_url = urljoin(page.final_url, str(iframe.get("src")))
        if not iframe_url.startswith(("http://", "https://")):
            return page
        return await self.fetch(iframe_url)

    @staticmethod
    def _select_cards(soup: BeautifulSoup) -> list[Tag]:
        for selector in _CARD_SELECTORS:
            try:
                elements = [
                    element
                    for element in soup.select(selector)
                    if _detail_link(element, "https://placeholder.invalid")
                ]
            except (ValueError, TypeError):
                continue
            if elements:
                return elements
        return []

    @staticmethod
    def parse_cards(html: str, page_url: str, *, raw_object: str | None = None) -> list[_ParsedCard]:
        page_url = str(page_url)
        soup = BeautifulSoup(html, "html.parser")
        elements = HTMLPortalScraper._select_cards(soup)
        cards: dict[str, _ParsedCard] = {}
        for element in elements:
            url = _detail_link(element, page_url)
            if url is None:
                continue
            text = _text(element)
            if len(text) < 8:
                continue
            link = next(
                (
                    anchor
                    for anchor in element.select("a[href]")
                    if urljoin(page_url, str(anchor.get("href"))) == url
                ),
                element.select_one("a[href]") or element,
            )
            cpvs = _cpvs(element, text)
            card = _ParsedCard(
                url=url,
                title=_title(element, link, text),
                client=_client(text, element),
                deadline=_deadline(text, element),
                cpv_codes=cpvs,
                publication_date=_published_date(text, element),
                procedure=_procedure(text),
                portal_id=_portal_id(element, url),
                text=text,
                raw_object=raw_object,
            )
            existing = cards.get(url)
            if existing is None or (len(card.cpv_codes), len(card.text)) > (
                len(existing.cpv_codes),
                len(existing.text),
            ):
                cards[url] = card
        return list(cards.values())

    def parse_listing(
        self,
        html: str,
        page_url: str,
        *,
        raw_object: str | None = None,
    ) -> list[_ParsedCard]:
        """Fixture-friendly alias for the static listing parser."""

        return self.parse_cards(html, str(page_url), raw_object=raw_object)

    @staticmethod
    def parse_attachment_links(html: str, page_url: str) -> list[_AttachmentLink]:
        """Find PDF/GAEB/ZIP links on a listing or detail page."""

        soup = BeautifulSoup(html, "html.parser")
        page_url = str(page_url)
        found: dict[str, _AttachmentLink] = {}
        for anchor in soup.select("a[href]"):
            href = str(anchor.get("href", "")).strip()
            lowered = href.casefold()
            if not href or any(reject in lowered for reject in _ATTACHMENT_REJECTS):
                continue
            path = urlsplit(href).path.casefold()
            if not path.endswith(_ATTACHMENT_SUFFIXES):
                # Some portals put the filename in the visible link text.
                text_suffix = Path(_clean(anchor.get_text(" ", strip=True))).suffix.casefold()
                if text_suffix not in _ATTACHMENT_SUFFIXES:
                    continue
            absolute = urljoin(page_url, href)
            filename = Path(urlsplit(absolute).path).name or "attachment"
            if not Path(filename).suffix:
                text_name = _clean(anchor.get_text(" ", strip=True))
                filename = Path(text_name).name or "attachment"
            if absolute not in found:
                found[absolute] = _AttachmentLink(url=absolute, filename=filename)
        return list(found.values())

    def _attachment_kind(self, artifact: ScrapedArtifact) -> str:
        suffix = Path(artifact.filename).suffix.casefold()
        if suffix in {".x83", ".d83", ".x84"}:
            return "gaeb"
        if suffix == ".pdf":
            return "pdf"
        if suffix == ".zip":
            return "archive"
        return "other"

    async def _harvest_attachments(
        self,
        page: ScrapedPage,
        *,
        limit: int,
    ) -> tuple[list[PortalAttachment], list[str], list[str]]:
        attachments: list[PortalAttachment] = []
        errors: list[str] = []
        object_names: list[str] = []
        for link in self.parse_attachment_links(page.text, page.final_url)[:limit]:
            try:
                artifact = await self.download_attachment(link.url, filename=link.filename)
            except Exception as exc:
                errors.append(f"{link.url}: attachment download failed: {exc}")
                continue
            attachments.append(
                PortalAttachment(
                    url=artifact.url,
                    filename=artifact.filename,
                    object_name=artifact.object_name,
                    content_type=artifact.content_type,
                    kind=self._attachment_kind(artifact),
                )
            )
            object_names.append(artifact.object_name)
        return attachments, errors, object_names

    def parse_detail(self, html: str, url: str, *, raw_object: str | None = None) -> _ParsedCard:
        url = str(url)
        soup = BeautifulSoup(html, "html.parser")
        text = _text(soup.body or soup)
        heading = next(
            (
                _clean(node.get_text(" ", strip=True))
                for node in soup.select("h1, h2, h3, h4, h5")
                if _clean(node.get_text(" ", strip=True)).casefold() not in _GENERIC_TITLES
            ),
            "",
        )
        return _ParsedCard(
            url=url,
            title=heading or "Unbenannte Ausschreibung",
            client=_client(text, soup),
            deadline=_deadline(text, soup),
            cpv_codes=_cpvs(soup, text),
            publication_date=_published_date(text, soup),
            procedure=_procedure(text),
            portal_id=hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
            text=text,
            raw_object=raw_object,
        )

    def _merge_detail(self, card: _ParsedCard, detail: _ParsedCard) -> _ParsedCard:
        return replace(
            card,
            title=card.title if card.title not in {"", "Unbenannte Ausschreibung"} else detail.title,
            client=card.client or detail.client,
            deadline=card.deadline or detail.deadline,
            cpv_codes=tuple(dict.fromkeys((*card.cpv_codes, *detail.cpv_codes))),
            publication_date=card.publication_date or detail.publication_date,
            procedure=card.procedure
            if card.procedure is not ProcurementProcedure.OPEN_PROCEDURE
            else detail.procedure,
            raw_object=detail.raw_object or card.raw_object,
            text=card.text if len(card.text) >= len(detail.text) else detail.text,
        )

    def _needs_detail(self, card: _ParsedCard) -> bool:
        return (
            not card.cpv_codes
            or card.deadline is None
            or not card.client
            or card.title == "Unbenannte Ausschreibung"
        )

    def _to_discovered(
        self,
        card: _ParsedCard,
        *,
        attachments: tuple[PortalAttachment, ...] = (),
        raw_objects: tuple[str, ...] = (),
    ) -> DiscoveredTender | None:
        matched = filter_hygiene_cpv(card.cpv_codes)
        if not matched or card.deadline is None:
            return None
        return DiscoveredTender(
            portal=self.portal_name,
            portal_code=self.portal_code,
            portal_id=card.portal_id,
            title=card.title,
            client_name=card.client or "Unbekannter Auftraggeber",
            detail_url=card.url,
            submission_deadline=card.deadline,
            cpv_codes=matched,
            publication_date=card.publication_date,
            procedure=card.procedure,
            raw_object=card.raw_object,
            raw_objects=raw_objects,
            attachments=attachments,
            metadata={
                "listing": "portal-html",
                "portal_id": card.portal_id,
            },
        )

    async def discover(
        self,
        *,
        max_pages: int | None = None,
        since: datetime | None = None,
    ) -> list[DiscoveredTender]:
        limit = max_pages or self.settings.scraper_max_pages
        page: ScrapedPage | None = await self._first_listing_page(self.start_url)
        visited: set[str] = set()
        seen_tenders: set[str] = set()
        discovered: list[DiscoveredTender] = []
        errors: list[str] = []
        skipped_cpv = 0
        skipped_invalid = 0
        skipped_stale = 0
        pages_fetched = 0
        since_date = since.date() if since is not None else None

        while page is not None and pages_fetched < limit:
            if page.final_url in visited:
                break
            visited.add(page.final_url)
            pages_fetched += 1
            soup = BeautifulSoup(page.text, "html.parser")
            cards = self.parse_cards(page.text, page.final_url, raw_object=page.archived_object)
            page_has_recent = False
            for card in cards:
                if card.url in seen_tenders:
                    continue
                if since_date is not None and card.publication_date is not None:
                    if card.publication_date < since_date:
                        skipped_stale += 1
                        continue
                    page_has_recent = True
                elif since_date is not None:
                    # Without a publication date we cannot prove the card is
                    # stale; keep paginating and rely on tender-number dedupe.
                    page_has_recent = True

                detail_page: ScrapedPage | None = None
                attachments: tuple[PortalAttachment, ...] = ()
                attachment_objects: list[str] = []
                needs_detail = self.fetch_details and self._needs_detail(card)
                should_fetch_detail = needs_detail or self.settings.scraper_harvest_attachments
                if should_fetch_detail:
                    try:
                        detail_page = await self.fetch(card.url)
                        detail = self.parse_detail(
                            detail_page.text,
                            detail_page.final_url,
                            raw_object=detail_page.archived_object,
                        )
                        if needs_detail:
                            card = self._merge_detail(card, detail)
                        if self.settings.scraper_harvest_attachments:
                            (
                                harvested,
                                attachment_errors,
                                attachment_objects,
                            ) = await self._harvest_attachments(
                                detail_page,
                                limit=self.settings.scraper_max_attachments,
                            )
                            attachments = tuple(harvested)
                            errors.extend(attachment_errors)
                    except Exception as exc:
                        errors.append(f"{card.url}: detail fetch failed: {exc}")
                        if needs_detail:
                            skipped_invalid += 1
                            continue

                if not filter_hygiene_cpv(card.cpv_codes):
                    skipped_cpv += 1
                    continue
                raw_objects = tuple(
                    name
                    for name in (
                        page.archived_object,
                        detail_page.archived_object if detail_page is not None else None,
                        *attachment_objects,
                    )
                    if name
                )
                try:
                    tender = self._to_discovered(
                        card,
                        attachments=attachments,
                        raw_objects=raw_objects,
                    )
                except Exception as exc:
                    errors.append(f"{card.url}: invalid metadata: {exc}")
                    skipped_invalid += 1
                    continue
                if tender is None:
                    skipped_invalid += 1
                    continue
                seen_tenders.add(card.url)
                discovered.append(tender)

            if since_date is not None and cards and not page_has_recent:
                break
            next_url = _next_page(soup, page.final_url)
            if next_url is None or next_url in visited:
                break
            try:
                page = await self.fetch(next_url)
            except Exception as exc:
                errors.append(f"{next_url}: pagination fetch failed: {exc}")
                break

        self.last_report = ScrapeReport(
            portal=self.portal_name,
            discovered=tuple(discovered),
            pages_fetched=pages_fetched,
            skipped_cpv=skipped_cpv,
            skipped_invalid=skipped_invalid,
            errors=tuple(errors),
            skipped_stale=skipped_stale,
        )
        return discovered
