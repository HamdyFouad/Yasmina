"""Leitweg-ID format and MOD-97-10 check-digit validation."""

from __future__ import annotations

import re

__all__ = ["LeitwegIdError", "validate_leitweg_id"]

_COARSE_RE = re.compile(r"^\d{2,12}$")
_FINE_RE = re.compile(r"^[A-Za-z0-9]{0,30}$")
_CHECK_RE = re.compile(r"^\d{2}$")


class LeitwegIdError(ValueError):
    """Raised when a Leitweg-ID is malformed or has an invalid check digit."""


def _numeric(value: str) -> int:
    """Convert letters to 10..35 and digits unchanged (ISO 7064 MOD 97-10)."""

    digits: list[str] = []
    for char in value:
        if char.isdigit():
            digits.append(char)
        elif "A" <= char.upper() <= "Z":
            digits.append(str(ord(char.upper()) - 55))
        else:
            raise LeitwegIdError(f"invalid Leitweg-ID character {char!r}")
    return int("".join(digits))


def validate_leitweg_id(value: str) -> str:
    """Validate and return a canonical Leitweg-ID.

    Structure: coarse address (2-12 digits), optional fine address (up to 30
    alphanumerics) and a two-digit MOD-97-10 check number, separated by hyphens.
    """

    if not isinstance(value, str):
        raise LeitwegIdError("Leitweg-ID must be a string")
    candidate = value.strip()
    if not candidate or candidate != value:
        raise LeitwegIdError("Leitweg-ID must not be empty or padded with whitespace")
    if not 5 <= len(candidate) <= 46:
        raise LeitwegIdError("Leitweg-ID must be 5-46 characters long")
    if " " in candidate:
        raise LeitwegIdError("Leitweg-ID must not contain spaces")

    parts = candidate.split("-")
    if len(parts) == 2:
        coarse, check = parts
        fine = ""
    elif len(parts) == 3:
        coarse, fine, check = parts
    else:
        raise LeitwegIdError("Leitweg-ID must have 2 or 3 hyphen-separated segments")

    if not _COARSE_RE.fullmatch(coarse):
        raise LeitwegIdError("coarse Leitweg-ID segment must be 2-12 digits")
    if not _FINE_RE.fullmatch(fine):
        raise LeitwegIdError("fine Leitweg-ID segment must be at most 30 alphanumerics")
    if not _CHECK_RE.fullmatch(check):
        raise LeitwegIdError("Leitweg-ID check segment must be two digits")
    if len(parts) == 3 and fine == "":
        raise LeitwegIdError("empty fine segment is only valid with two segments")

    numeric = coarse + fine + check
    if _numeric(numeric) % 97 != 1:
        raise LeitwegIdError("Leitweg-ID check digit is invalid")

    return candidate
