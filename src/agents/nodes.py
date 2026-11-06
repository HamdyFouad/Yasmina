"""LangGraph nodes for tender enrichment, matching, pricing and approval."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError

from src.agents.state import AgentContext, AgentState, AgentStatus
from src.models.enums import (
    CustomerOfferStatus,
    StandardizedColor,
)
from src.models.schemas import (
    CustomerOffer,
    CustomerOfferLine,
    SupplierQuoteLine,
    Tender,
    TenderItem,
    q2,
)
from src.parsers.gaeb_parser import write_x84
from src.parsers.normalizers import (
    normalize_color,
    normalize_glove_size,
    normalize_material,
)
from src.services.pricing_service import select_tier_price
from src.services.unit_converter import UnitConversionError

__all__ = [
    "approval_gate",
    "enrich_specs",
    "export_offer",
    "match_catalog",
    "price_offer",
    "route_after_approval",
    "route_after_price",
    "route_after_triage",
    "triage_tender",
]

_THICKNESS_RE = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>µm|um|mikron|micron|mm)?", re.IGNORECASE)
_PACK_RE = re.compile(r"(\d+(?:[.,]\d{3})*)")


def _append(state: AgentState, key: str, value: str) -> list[str]:
    return [*cast(list[str], state.get(key, [])), value]


def _spec_attr(raw: Any, name: str) -> str | None:
    value = getattr(raw, name, None)
    return str(value).strip() if value is not None and str(value).strip() else None


def _thickness(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = _THICKNESS_RE.search(value)
    if match is None:
        return None
    try:
        number = Decimal(match.group("value").replace(",", "."))
    except InvalidOperation:
        return None
    if match.group("unit") and match.group("unit").lower() == "mm":
        number *= Decimal(1000)
    if number < 1 or number > 2000:
        return None
    return number.quantize(Decimal("0.01"))


def _pack_size(value: str | None) -> int | None:
    if not value:
        return None
    match = _PACK_RE.search(value)
    if match is None:
        return None
    try:
        number = Decimal(match.group(1).replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None
    if number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


def _enrich_item(item: TenderItem, raw: Any) -> TenderItem:
    updates: dict[str, Any] = {}

    if item.material is None:
        material = normalize_material(_spec_attr(raw, "material") or "")
        if material.value != "NOT_APPLICABLE":
            updates["material"] = material
    if item.color is None:
        color = normalize_color(_spec_attr(raw, "color") or "")
        if color is not StandardizedColor.UNKNOWN:
            updates["color"] = color
    if item.glove_size.value == "NOT_APPLICABLE":
        glove_size = normalize_glove_size(_spec_attr(raw, "glove_size") or "")
        if glove_size.value != "NOT_APPLICABLE":
            updates["glove_size"] = glove_size
    if item.thickness_um is None:
        thickness = _thickness(_spec_attr(raw, "thickness"))
        if thickness is not None:
            updates["thickness_um"] = thickness
    if item.pack_size is None:
        pack_size = _pack_size(_spec_attr(raw, "pack_size"))
        if pack_size is not None:
            updates["pack_size"] = pack_size

    return item.model_copy(update=updates) if updates else item


def _triage_text(tender: Tender) -> str:
    item_text = " ".join(
        f"{item.position_number} {item.short_text} {item.long_text or ''}".strip() for item in tender.items
    )
    return (
        f"Title: {tender.title}\n"
        f"Client: {tender.client_name}\n"
        f"Description: {tender.description or ''}\n"
        f"CPV: {', '.join(tender.cpv_codes) or 'none'}\n"
        f"Positions: {item_text}"
    )[:12000]


def triage_tender(state: AgentState, context: AgentContext) -> dict[str, Any]:
    """Optional live-LLM relevance classification before enrichment/pricing."""

    tender = state.get("tender")
    if tender is None or context.llm is None:
        return {}
    method = getattr(context.llm, "triage_tender", None)
    if not callable(method):
        return {}

    try:
        result = method(_triage_text(tender), cpv_codes=list(tender.cpv_codes))
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    except Exception as exc:
        return {
            "warnings": _append(
                state,
                "warnings",
                f"LLM triage failed; continuing deterministically: {exc}",
            )
        }

    if not bool(data.get("is_relevant", True)):
        return {
            "status": AgentStatus.TRIAGE_REJECTED,
            "triage": data,
            "errors": _append(
                state,
                "errors",
                f"tender triaged as not relevant: {data.get('reason', 'no reason supplied')}",
            ),
        }
    return {"triage": data}


def enrich_specs(state: AgentState, context: AgentContext) -> dict[str, Any]:
    """Optional LLM extraction of missing material/colour/size/pack hints."""

    tender = state.get("tender")
    if tender is None:
        return {"status": AgentStatus.FAILED, "errors": ["no tender in agent state"]}
    if context.llm is None:
        return {}

    warnings = list(state.get("warnings", []))
    items: list[TenderItem] = []
    changed = False
    for item in tender.items:
        needs_enrichment = (
            item.material is None
            or item.color is None
            or item.glove_size.value == "NOT_APPLICABLE"
            or item.thickness_um is None
            or item.pack_size is None
        )
        if not needs_enrichment:
            items.append(item)
            continue
        try:
            raw = context.llm.extract_item_specs(f"{item.short_text} {item.long_text or ''}".strip())
            enriched = _enrich_item(item, raw)
        except Exception as exc:  # LLM/network failures must not abort deterministic pricing
            warnings.append(f"LLM enrichment failed for position {item.position_number}: {exc}")
            items.append(item)
            continue
        if enriched != item:
            changed = True
        items.append(enriched)

    if not changed:
        return {"warnings": warnings}
    try:
        enriched_tender = Tender.model_validate(tender.model_copy(update={"items": items}).model_dump())
    except ValidationError as exc:
        warnings.append(f"enriched tender failed validation, keeping original: {exc}")
        return {"warnings": warnings}
    return {"tender": enriched_tender, "warnings": warnings}


def match_catalog(state: AgentState, context: AgentContext) -> dict[str, Any]:
    """Populate review candidates for every tender position (no mutations)."""

    tender = state.get("tender")
    catalog = state.get("catalog", [])
    if tender is None:
        return {"status": AgentStatus.FAILED, "errors": ["no tender in agent state"]}
    if not catalog:
        return {}

    matches = {}
    for item in tender.items:
        results = context.matcher.rank(item, catalog, limit=5)
        if results:
            matches[item.position_number] = results
    return {"matches": matches}


def _best_quote_line(
    quotes: list[SupplierQuoteLine],
    item: TenderItem,
) -> SupplierQuoteLine | None:
    eligible = [line for line in quotes if line.unit is item.unit]
    if not eligible:
        return None

    def sort_key(line: SupplierQuoteLine) -> tuple[Decimal, int]:
        price, _ = select_tier_price(line, item.quantity)
        return price, line.line_number

    return min(eligible, key=sort_key)


def price_offer(state: AgentState, context: AgentContext) -> dict[str, Any]:
    """Price all tender positions and build an unsigned draft offer."""

    tender = state.get("tender")
    request = state.get("offer_request")
    quotes = state.get("supplier_quotes", [])
    if tender is None or request is None:
        return {"status": AgentStatus.FAILED, "errors": ["tender and offer_request are required"]}

    errors = list(state.get("errors", []))
    warnings = list(state.get("warnings", []))

    existing = state.get("offer")
    if existing is not None and existing.status in {
        CustomerOfferStatus.APPROVED,
        CustomerOfferStatus.SENT,
        CustomerOfferStatus.ACCEPTED,
    }:
        return {}

    lines_by_item: dict[UUID, list[SupplierQuoteLine]] = {}
    for quote in quotes:
        for line in quote.lines:
            if line.tender_item_id is not None:
                lines_by_item.setdefault(line.tender_item_id, []).append(line)

    offer_lines: list[CustomerOfferLine] = []
    for item in tender.items:
        candidates = lines_by_item.get(item.id, [])
        if not candidates:
            errors.append(f"no supplier quote line linked to position {item.position_number}")
            continue
        eligible: list[SupplierQuoteLine] = []
        conversion_errors: list[str] = []
        for candidate in candidates:
            try:
                eligible.append(context.converter.convert_line(candidate, item.unit))
            except UnitConversionError as exc:
                conversion_errors.append(str(exc))
        if not eligible:
            detail = "; ".join(conversion_errors) if conversion_errors else "no matching unit"
            errors.append(f"unit conversion required for position {item.position_number}: {detail}")
            continue
        best_line = _best_quote_line(eligible, item)
        if best_line is None:
            errors.append(f"no usable quote line for position {item.position_number}")
            continue

        priced = context.pricing.price_line(
            best_line,
            quantity=item.quantity,
            margin_rate=request.margin_rate,
        )
        offer_lines.append(
            CustomerOfferLine(
                position_number=item.position_number,
                description=item.short_text,
                tender_item_id=item.id,
                supplier_quote_line_id=best_line.id,
                quantity=item.quantity,
                unit=best_line.unit,
                cost_unit_price_net=priced.cost_unit_price_net,
                margin_rate=priced.margin_rate,
                unit_price_net=priced.unit_price_net,
                total_net=priced.total_net,
                vat_rate=request.vat_rate,
            )
        )

    if errors or not offer_lines:
        return {"status": AgentStatus.FAILED, "errors": errors, "warnings": warnings}

    total_net = q2(sum((offer_line.total_net for offer_line in offer_lines), start=Decimal("0.00")))
    by_rate: dict[Decimal, Decimal] = {}
    for offer_line in offer_lines:
        by_rate[offer_line.vat_rate] = (
            by_rate.get(offer_line.vat_rate, Decimal("0.00")) + offer_line.total_net
        )
    total_vat = q2(sum((q2(base * rate) for rate, base in by_rate.items()), start=Decimal("0.00")))
    total_gross = q2(total_net + total_vat)
    try:
        offer = CustomerOffer(
            offer_number=request.offer_number,
            customer_name=request.customer_name,
            customer_contact_email=request.customer_contact_email,
            customer_reference=tender.tender_number,
            tender_id=tender.id,
            status=CustomerOfferStatus.DRAFT,
            currency=tender.currency,
            lines=offer_lines,
            margin_rate=request.margin_rate,
            total_net=total_net,
            vat_rate=request.vat_rate,
            total_vat=total_vat,
            total_gross=total_gross,
            valid_until=request.valid_until,
        )
    except ValidationError as exc:
        errors.append(f"draft offer failed validation: {exc}")
        return {"status": AgentStatus.FAILED, "errors": errors, "warnings": warnings}

    return {"offer": offer, "errors": errors, "warnings": warnings}


def approval_gate(state: AgentState, context: AgentContext) -> dict[str, Any]:
    """Route an offer to export only after cryptographic approval."""

    offer = state.get("offer")
    if offer is None:
        return {"status": AgentStatus.FAILED, "errors": _append(state, "errors", "no offer to approve")}

    if not context.approval.configured:
        return {
            "status": AgentStatus.PENDING_APPROVAL,
            "warnings": _append(state, "warnings", "no Ed25519 public key configured; approval required"),
        }

    if context.approval.verify(offer):
        return {"status": AgentStatus.APPROVED}

    if offer.ed25519_approval_signature is not None:
        return {
            "status": AgentStatus.APPROVAL_INVALID,
            "errors": _append(state, "errors", "offer signature does not verify against the configured key"),
        }

    return {
        "status": AgentStatus.PENDING_APPROVAL,
        "warnings": _append(state, "warnings", "offer is unsigned and awaits human approval"),
    }


def export_offer(state: AgentState, context: AgentContext) -> dict[str, Any]:
    """Export an approved offer as GAEB X84 when a source document/path is present."""

    offer = state.get("offer")
    if offer is None:
        return {"status": AgentStatus.FAILED, "errors": _append(state, "errors", "no offer to export")}
    if not context.approval.verify(offer):
        return {
            "status": AgentStatus.APPROVAL_INVALID,
            "errors": _append(state, "errors", "refusing to export an unverified offer"),
        }

    document = state.get("gaeb_document")
    output = state.get("export_path")
    if document is None or output is None:
        return {
            "status": AgentStatus.APPROVED,
            "warnings": _append(state, "warnings", "offer approved; no GAEB document/output configured"),
        }

    try:
        warnings = write_x84(
            document,
            offer,
            output,
            approval_service=context.approval,
        )
    except Exception as exc:
        return {
            "status": AgentStatus.FAILED,
            "errors": _append(state, "errors", f"X84 export failed: {exc}"),
        }
    return {
        "status": AgentStatus.EXPORTED,
        "exported_path": str(output),
        "warnings": [*state.get("warnings", []), *warnings],
    }


def route_after_price(state: AgentState) -> str:
    return "approval" if state.get("offer") is not None else "stop"


def route_after_triage(state: AgentState) -> str:
    return "stop" if state.get("status") == AgentStatus.TRIAGE_REJECTED else "enrich"


def route_after_approval(state: AgentState) -> str:
    return "export" if state.get("status") == AgentStatus.APPROVED else "stop"
