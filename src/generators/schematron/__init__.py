"""Offline execution of the official KoSIT XRechnung Schematron rules.

The compiled KoSIT XSLT rule sets are XSLT 3.0 and therefore require Saxon-HE.
They are vendored in :mod:`src.generators.schematron.kosit` so validation does
not depend on the factur-x Saxon server or a Java runtime.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lxml import etree

__all__ = [
    "KoSITSchematronError",
    "KoSITSchematronValidator",
    "KoSITValidationReport",
    "SchematronExecutionError",
    "SchematronIssue",
    "SchematronUnavailableError",
]

Syntax = Literal["ubl", "cii"]

_ASSETS: dict[Syntax, tuple[str, ...]] = {
    "ubl": ("EN16931-UBL-validation.xsl", "XRechnung-UBL-validation.xsl"),
    "cii": ("EN16931-CII-validation.xsl", "XRechnung-CII-validation.xsl"),
}
_ERROR_LEVELS = frozenset({"fatal", "error"})


class KoSITSchematronError(ValueError):
    """Raised when an e-invoice fails the KoSIT Schematron rules."""

    def __init__(self, report: "KoSITValidationReport") -> None:
        self.report = report
        details = "; ".join(
            f"[{issue.rule_id}] {issue.message}" for issue in report.errors[:10]
        )
        super().__init__(f"KoSIT XRechnung Schematron validation failed: {details}")


class SchematronUnavailableError(RuntimeError):
    """Raised when the Saxon-HE XSLT 3.0 processor is not installed."""


class SchematronExecutionError(RuntimeError):
    """Raised when a KoSIT Schematron XSLT cannot be executed."""


@dataclass(frozen=True, slots=True)
class SchematronIssue:
    rule_id: str
    message: str
    location: str | None
    severity: str
    ruleset: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "location": self.location,
            "severity": self.severity,
            "ruleset": self.ruleset,
        }


@dataclass(frozen=True, slots=True)
class KoSITValidationReport:
    valid: bool
    issues: tuple[SchematronIssue, ...]
    checked_rulesets: tuple[str, ...]

    @property
    def errors(self) -> tuple[SchematronIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity in _ERROR_LEVELS)

    @property
    def warnings(self) -> tuple[SchematronIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity not in _ERROR_LEVELS)

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "checked_rulesets": list(self.checked_rulesets),
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }


class KoSITSchematronValidator:
    """Run EN16931 + XRechnung CIUS Schematron rule sets for UBL or CII."""

    def __init__(self, assets_dir: str | Path | None = None) -> None:
        self.assets_dir = Path(assets_dir) if assets_dir is not None else _default_assets_dir()

    def validate(self, xml: bytes, *, syntax: Syntax = "ubl") -> KoSITValidationReport:
        if syntax not in _ASSETS:
            raise ValueError(f"unsupported Schematron syntax {syntax!r}")

        try:
            from saxonche import PySaxonProcessor
        except ImportError as exc:  # pragma: no cover - depends on installation
            raise SchematronUnavailableError(
                "saxonche is required for KoSIT Schematron validation; "
                "install the 'e-invoicing' extra"
            ) from exc

        rulesets = _ASSETS[syntax]
        missing = [name for name in rulesets if not (self.assets_dir / name).is_file()]
        if missing:
            raise SchematronExecutionError(
                f"KoSIT Schematron assets are missing: {', '.join(missing)}"
            )

        issues: list[SchematronIssue] = []
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
                handle.write(xml)
                temp_path = Path(handle.name)
            with PySaxonProcessor(license=False) as processor:
                xslt = processor.new_xslt30_processor()
                for name in rulesets:
                    result = xslt.transform_to_string(
                        source_file=str(temp_path),
                        stylesheet_file=str(self.assets_dir / name),
                    )
                    issues.extend(_parse_svrl(result, ruleset=name))
        except (KoSITSchematronError, SchematronUnavailableError, SchematronExecutionError):
            raise
        except Exception as exc:
            raise SchematronExecutionError(f"KoSIT Schematron execution failed: {exc}") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        report = KoSITValidationReport(
            valid=not any(issue.severity in _ERROR_LEVELS for issue in issues),
            issues=tuple(issues),
            checked_rulesets=tuple(rulesets),
        )
        return report

    def validate_or_raise(self, xml: bytes, *, syntax: Syntax = "ubl") -> KoSITValidationReport:
        report = self.validate(xml, syntax=syntax)
        if not report.valid:
            raise KoSITSchematronError(report)
        return report


def _default_assets_dir() -> Path:
    return Path(__file__).resolve().parent / "kosit"


def _parse_svrl(svrl: str, *, ruleset: str) -> list[SchematronIssue]:
    try:
        root = etree.fromstring(svrl.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        raise SchematronExecutionError(f"KoSIT Schematron returned invalid SVRL: {exc}") from exc

    issues: list[SchematronIssue] = []
    for element in root.xpath(".//*[local-name()='failed-assert']"):
        rule_id = element.get("id") or element.get("test") or "unknown"
        message = " ".join("".join(element.itertext()).split()) or "Schematron rule failed"
        severity = (element.get("flag") or "error").strip().lower()
        issues.append(
            SchematronIssue(
                rule_id=rule_id,
                message=message,
                location=element.get("location"),
                severity=severity,
                ruleset=ruleset,
            )
        )
    return issues
