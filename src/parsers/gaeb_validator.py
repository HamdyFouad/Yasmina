"""GAEB validation: structural checks, penny reconciliation and optional XSD.

The validator is deliberately independent from the parser: it can validate a
freshly parsed document before pricing or a written bid after export. Decimal
comparisons use a one-cent tolerance, matching GAEB's monetary precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from src.models.schemas import q2

__all__ = [
    "GaebValidationError",
    "GaebValidationIssue",
    "GaebValidationReport",
    "validate_gaeb",
    "validate_gaeb_file",
]

PENNY = Decimal("0.01")
Severity = Literal["error", "warning"]


class GaebValidationError(ValueError):
    """Raised when a validation report is explicitly converted to an exception."""

    def __init__(self, report: GaebValidationReport) -> None:
        self.report = report
        messages = "; ".join(issue.message for issue in report.errors[:5])
        super().__init__(f"GAEB validation failed: {messages}")


@dataclass(frozen=True, slots=True)
class GaebValidationIssue:
    code: str
    message: str
    severity: Severity
    oz: str | None = None


@dataclass(frozen=True, slots=True)
class GaebValidationReport:
    issues: tuple[GaebValidationIssue, ...] = field(default_factory=tuple)
    xsd_checked: bool = False

    @property
    def errors(self) -> tuple[GaebValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[GaebValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def valid(self) -> bool:
        return not self.errors

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise GaebValidationError(self)


def _issue(
    code: str,
    message: str,
    severity: Severity = "error",
    *,
    oz: str | None = None,
) -> GaebValidationIssue:
    return GaebValidationIssue(code=code, message=message, severity=severity, oz=oz)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return None


def _differs(left: Decimal, right: Decimal, tolerance: Decimal = PENNY) -> bool:
    return abs(left - right) >= tolerance


def _item_total(item: Any) -> Decimal | None:
    return _decimal(getattr(item, "total_price", None))


def _collect_totals(document: Any) -> list[Any]:
    totals: list[Any] = []
    award = getattr(document, "award", None)
    boq = getattr(award, "boq", None)
    if boq is None:
        return totals

    boq_info = getattr(boq, "boq_info", None)
    if boq_info is not None and getattr(boq_info, "totals", None) is not None:
        totals.append(boq_info.totals)
    for lot in getattr(boq, "lots", ()) or ():
        if getattr(lot, "totals", None) is not None:
            totals.append(lot.totals)
    return totals


def _check_item_math(items: list[Any], issues: list[GaebValidationIssue]) -> None:
    for item in items:
        oz = str(getattr(item, "full_oz", None) or getattr(item, "oz", "?"))
        qty = _decimal(getattr(item, "qty", None))
        unit_price = _decimal(getattr(item, "unit_price", None))
        total = _item_total(item)

        if qty is None or qty <= 0:
            issues.append(_issue("ITEM_QUANTITY", f"position {oz} has no positive quantity", oz=oz))
            continue
        if unit_price is not None and total is not None:
            expected = q2(qty * unit_price)
            if _differs(expected, total):
                issues.append(
                    _issue(
                        "ITEM_TOTAL_MISMATCH",
                        f"position {oz}: qty*unit_price={expected}, total_price={total}",
                        oz=oz,
                    )
                )


def _check_boq_totals(document: Any, items: list[Any], issues: list[GaebValidationIssue]) -> None:
    expected = sum(
        (_item_total(item) or Decimal("0.00") for item in items if getattr(item, "affects_total", True)),
        start=Decimal("0.00"),
    )
    expected = q2(expected)

    grand_total = _decimal(getattr(document, "grand_total", None))
    computed = _decimal(getattr(document, "computed_grand_total", None))
    priced_items = any(_item_total(item) is not None for item in items)

    if grand_total is not None and priced_items and _differs(expected, grand_total):
        issues.append(
            _issue(
                "GRAND_TOTAL_MISMATCH",
                f"sum of positions={expected}, document grand_total={grand_total}",
            )
        )
    if computed is not None and priced_items and _differs(expected, computed):
        issues.append(
            _issue(
                "COMPUTED_TOTAL_MISMATCH",
                f"sum of positions={expected}, computed_grand_total={computed}",
            )
        )

    for totals in _collect_totals(document):
        net = _decimal(getattr(totals, "total_net", None))
        vat = _decimal(getattr(totals, "vat_amount", None))
        gross = _decimal(getattr(totals, "total_gross", None))
        if net is not None and vat is not None and gross is not None and _differs(q2(net + vat), gross):
            issues.append(
                _issue(
                    "VAT_TOTAL_MISMATCH",
                    f"net+VAT={q2(net + vat)}, gross={gross}",
                )
            )


def _check_xsd(
    document: Any,
    xsd_dir: str | Path | None,
    require_xsd: bool,
    issues: list[GaebValidationIssue],
) -> bool:
    if xsd_dir is None:
        severity: Severity = "error" if require_xsd else "warning"
        issues.append(
            _issue(
                "XSD_NOT_CONFIGURED",
                "no xsd_dir supplied; schema validation was skipped",
                severity,
            )
        )
        return False

    try:
        from pygaeb import GAEBWriter
    except ImportError:  # pragma: no cover - optional dependency
        issues.append(
            _issue("XSD_UNAVAILABLE", "pyGAEB is not installed", "error" if require_xsd else "warning")
        )
        return False

    result = GAEBWriter.validate_against_xsd(
        document,
        phase=getattr(document, "exchange_phase", None),
        xsd_dir=xsd_dir,
    )
    if result is None:
        severity = "error" if require_xsd else "warning"
        issues.append(
            _issue(
                "XSD_SCHEMA_NOT_FOUND",
                f"no matching GAEB schema found under {xsd_dir}",
                severity,
            )
        )
        return False
    for error in getattr(result, "errors", ()):
        line = getattr(error, "line", None)
        location = f"line {line}: " if line is not None else ""
        issues.append(_issue("XSD_INVALID", f"{location}{getattr(error, 'message', error)}"))
    return True


def validate_gaeb(
    document: Any,
    *,
    xsd_dir: str | Path | None = None,
    require_xsd: bool = False,
) -> GaebValidationReport:
    """Validate a pyGAEB document and reconcile its monetary totals."""

    issues: list[GaebValidationIssue] = []
    items = list(document.iter_items())

    seen: set[str] = set()
    for item in items:
        oz = str(getattr(item, "full_oz", None) or getattr(item, "oz", "?"))
        if oz in seen:
            issues.append(_issue("DUPLICATE_POSITION", f"duplicate ordinal number {oz}", oz=oz))
        seen.add(oz)

    _check_item_math(items, issues)
    _check_boq_totals(document, items, issues)
    xsd_checked = _check_xsd(document, xsd_dir, require_xsd, issues)
    return GaebValidationReport(issues=tuple(issues), xsd_checked=xsd_checked)


def validate_gaeb_file(
    path: str | Path,
    *,
    xsd_dir: str | Path | None = None,
    require_xsd: bool = False,
) -> GaebValidationReport:
    """Parse and validate a GAEB file from disk."""

    try:
        from pygaeb import GAEBParser
    except ImportError as exc:  # pragma: no cover - optional dependency
        return GaebValidationReport(issues=(_issue("GAEB_UNAVAILABLE", f"pyGAEB is not installed: {exc}"),))

    try:
        document = GAEBParser.parse(path)
    except Exception as exc:
        return GaebValidationReport(issues=(_issue("GAEB_PARSE", f"cannot parse {path}: {exc}"),))

    return validate_gaeb(document, xsd_dir=xsd_dir, require_xsd=require_xsd)
