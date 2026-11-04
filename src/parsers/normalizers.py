"""Pre-validation normalization layer for Project Yasmina.

Raw German extraction output (Docling PDF tables, ``pygaeb`` items, LLM
structured output) enters the system as free text such as ``"Nitril"``,
``"Gr. 8"``, ``"19 %"``, ``"transparent/klar"`` or ``"Karton à 1000 Stk"``.
Because every domain schema is declared with ``ConfigDict(strict=True,
extra="forbid")``, that text can never be handed to a Pydantic model directly:
it must first be mapped onto the canonical enums in :mod:`src.models.enums` and
onto ``decimal.Decimal`` values.

This module is deliberately pure and deterministic: no I/O, no network, no LLM
calls. Parsers and LLM nodes call :func:`normalize_raw_extraction` on the raw
dictionary, then hand the result to Pydantic.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from src.models.enums import (
    GloveSize,
    MaterialType,
    StandardizedColor,
    UnitOfMeasure,
)

__all__ = [
    "NormalizationError",
    "GLOVE_SIZE_EN420_MAP",
    "GERMAN_COLOR_MAP",
    "GERMAN_MATERIAL_MAP",
    "GERMAN_UNIT_MAP",
    "normalize_glove_size",
    "normalize_color",
    "normalize_material",
    "normalize_unit_of_measure",
    "normalize_decimal",
    "normalize_percentage",
    "normalize_pack_size",
    "normalize_quantity",
    "normalize_raw_extraction",
]


class NormalizationError(ValueError):
    """Raised when a raw extraction value cannot be normalized deterministically."""


# ---------------------------------------------------------------------------
# Canonical lookup tables
# ---------------------------------------------------------------------------

GLOVE_SIZE_EN420_MAP: dict[str, GloveSize] = {
    "5": GloveSize.XS, "5.5": GloveSize.XS, "6": GloveSize.XS, "6.0": GloveSize.XS, "xs": GloveSize.XS,
    "6.5": GloveSize.S, "7": GloveSize.S, "7.0": GloveSize.S, "s": GloveSize.S, "klein": GloveSize.S,
    "7.5": GloveSize.M, "8": GloveSize.M, "8.0": GloveSize.M, "m": GloveSize.M, "mittel": GloveSize.M,
    "8.5": GloveSize.L, "9": GloveSize.L, "9.0": GloveSize.L, "l": GloveSize.L, "groß": GloveSize.L, "gross": GloveSize.L,
    "9.5": GloveSize.XL, "10": GloveSize.XL, "10.0": GloveSize.XL, "xl": GloveSize.XL,
    "10.5": GloveSize.XXL, "11": GloveSize.XXL, "11.0": GloveSize.XXL, "xxl": GloveSize.XXL,
}

GERMAN_COLOR_MAP: dict[str, StandardizedColor] = {
    "blau": StandardizedColor.BLUE,
    "schwarz": StandardizedColor.BLACK,
    "gelb": StandardizedColor.YELLOW,
    "rot": StandardizedColor.RED,
    "grün": StandardizedColor.GREEN, "gruen": StandardizedColor.GREEN,
    "grau": StandardizedColor.GREY, "anthrazit": StandardizedColor.GREY,
    "braun": StandardizedColor.BROWN,
    "orange": StandardizedColor.ORANGE,
    "rosa": StandardizedColor.PINK, "pink": StandardizedColor.PINK,
    "violett": StandardizedColor.PURPLE, "lila": StandardizedColor.PURPLE,
    "weiß": StandardizedColor.WHITE, "weiss": StandardizedColor.WHITE, "hochweiß": StandardizedColor.WHITE,
    "transparent": StandardizedColor.TRANSPARENT, "transluzent": StandardizedColor.TRANSPARENT,
    "klar": StandardizedColor.CLEAR, "glasklar": StandardizedColor.CLEAR,
    "natur": StandardizedColor.NATURAL,
    "bedruckt": StandardizedColor.PRINTED_MULTICOLOR, "mehrfarbig": StandardizedColor.PRINTED_MULTICOLOR,
}

GERMAN_MATERIAL_MAP: dict[str, MaterialType] = {
    "nitril": MaterialType.NITRILE, "nitrile": MaterialType.NITRILE,
    "nitrilkautschuk": MaterialType.NITRILE, "nbr": MaterialType.NITRILE,
    "latex": MaterialType.LATEX, "naturlatex": MaterialType.LATEX,
    "naturkautschuk": MaterialType.LATEX,
    "vinyl": MaterialType.VINYL,
    "neopren": MaterialType.NEOPRENE, "neoprene": MaterialType.NEOPRENE,
    "butyl": MaterialType.BUTYL,
    "pvc": MaterialType.PVC, "pvc-folie": MaterialType.PVC,
    "polyethylen": MaterialType.POLYETHYLENE,
    "pe": MaterialType.POLYETHYLENE, "ldpe": MaterialType.POLYETHYLENE,
    "hdpe": MaterialType.POLYETHYLENE,
    "polypropylen": MaterialType.POLYPROPYLENE, "pp": MaterialType.POLYPROPYLENE,
    "papier": MaterialType.PAPER, "zellstoff": MaterialType.PAPER,
    "recyclingpapier": MaterialType.RECYCLED_PAPER, "altpapier": MaterialType.RECYCLED_PAPER,
    "baumwolle": MaterialType.COTTON, "cotton": MaterialType.COTTON,
    "mikrofaser": MaterialType.MICROFIBER, "microfaser": MaterialType.MICROFIBER,
    "gemischt": MaterialType.MIXED,
}

GERMAN_UNIT_MAP: dict[str, UnitOfMeasure] = {
    "stück": UnitOfMeasure.STUECK, "stueck": UnitOfMeasure.STUECK,
    "stk": UnitOfMeasure.STUECK, "stck": UnitOfMeasure.STUECK,
    "pack": UnitOfMeasure.PACK, "packung": UnitOfMeasure.PACK,
    "pkg": UnitOfMeasure.PACK,
    "karton": UnitOfMeasure.KARTON, "umkarton": UnitOfMeasure.KARTON,
    "palette": UnitOfMeasure.PALETTE,
    "rolle": UnitOfMeasure.ROLLE, "rollen": UnitOfMeasure.ROLLE,
    "kg": UnitOfMeasure.KILOGRAMM, "kilogramm": UnitOfMeasure.KILOGRAMM,
    "kilo": UnitOfMeasure.KILOGRAMM,
    "l": UnitOfMeasure.LITER, "liter": UnitOfMeasure.LITER, "lt": UnitOfMeasure.LITER,
    "m": UnitOfMeasure.METER, "meter": UnitOfMeasure.METER, "lfm": UnitOfMeasure.METER,
    "m²": UnitOfMeasure.QUADRATMETER, "m2": UnitOfMeasure.QUADRATMETER,
    "qm": UnitOfMeasure.QUADRATMETER, "quadratmeter": UnitOfMeasure.QUADRATMETER,
}

_FOLD_TRANSLATION: Final = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
)

_GLOVE_LABEL_RE: Final = re.compile(
    r"^(?:handschuh\s*)?(?:gr(?:ö|oe)?(?:ß|ss)?e?\.?|gr\.?|size|sz\.?)\s*"
)
_LEADING_NUMBER_RE: Final = re.compile(r"\d+(?:[.,]\d+)?")
_TOKEN_SPLIT_RE: Final = re.compile(
    r"\s*(?:/|\||,|;|\+|\bund\b|\boder\b|\bmit\b)\s*"
)
_THOUSANDS_DOT_RE: Final = re.compile(r"-?\d{1,3}(?:\.\d{3})+")
_THOUSANDS_COMMA_RE: Final = re.compile(r"-?\d{1,3}(?:,\d{3})+")
_CURRENCY_RE: Final = re.compile(r"[€$£]|\b(?:eur|euro)\b")
_PACK_CONTENT_RE: Final = re.compile(
    r"(?:à|\bje\b|inhalt\s*[:\s]|=\s*|\bmit\b)\s*(?P<value>\d+(?:[.,]\d{3})*)",
    re.IGNORECASE,
)
_ANY_NUMBER_RE: Final = re.compile(r"\d+(?:[.,]\d+)*")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Fold German umlauts and ``ß`` so both spellings resolve."""

    return text.translate(_FOLD_TRANSLATION)


def _clean_text(raw: Any) -> str:
    """Lowercase, collapse whitespace and trim stray punctuation."""

    text = str(raw).strip().lower()
    text = text.replace("\u00a0", " ").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,:;-_")


def _folded_table(table: Mapping[str, Any]) -> dict[str, Any]:
    """Index a lookup table by its umlaut-folded keys."""

    return {_fold(key): value for key, value in table.items()}


_FOLDED_GLOVE_SIZE_MAP: Final = _folded_table(GLOVE_SIZE_EN420_MAP)
_FOLDED_COLOR_MAP: Final = _folded_table(GERMAN_COLOR_MAP)
_FOLDED_MATERIAL_MAP: Final = _folded_table(GERMAN_MATERIAL_MAP)
_FOLDED_UNIT_MAP: Final = _folded_table(GERMAN_UNIT_MAP)


def _lookup(
    key: str,
    table: Mapping[str, Any],
    folded_table: Mapping[str, Any],
) -> Any | None:
    """Exact match first, then umlaut-folded match."""

    if not key:
        return None
    value = table.get(key)
    if value is not None:
        return value
    return folded_table.get(_fold(key))


def _keyword_scan(text: str, table: Mapping[str, Any]) -> Any | None:
    """Find the longest known keyword that occurs as a whole word."""

    for keyword in sorted(table, key=len, reverse=True):
        pattern = r"(?<![a-zäöüß])" + re.escape(keyword) + r"(?![a-zäöüß])"
        if re.search(pattern, text):
            return table[keyword]
    return None


def _candidates(text: str) -> list[str]:
    """Return the cleaned string plus its compound-separated segments."""

    segments = [segment for segment in _TOKEN_SPLIT_RE.split(text) if segment]
    return [text, *(_clean_text(segment) for segment in segments)]


def _strip_non_numeric(text: str) -> str:
    """Keep only digits, separators, sign and exponent markers."""

    return re.sub(r"[^0-9,.\-]", "", text)


# ---------------------------------------------------------------------------
# Enum normalizers
# ---------------------------------------------------------------------------


def normalize_glove_size(raw: str | int | float | Decimal | None) -> GloveSize:
    """Map an EN 420 size (``"Gr. 8"``, ``8``, ``"8,5"``, ``"M"``) to :class:`GloveSize`.

    ``None`` and unrecognizable values yield ``GloveSize.NOT_APPLICABLE``.
    """

    if raw is None:
        return GloveSize.NOT_APPLICABLE

    cleaned = _clean_text(raw).replace(",", ".")

    # Direct hit first: words such as "groß"/"gross" start with the "gr" label.
    size = _lookup(cleaned, GLOVE_SIZE_EN420_MAP, _FOLDED_GLOVE_SIZE_MAP)
    if size is not None:
        return size

    cleaned = _GLOVE_LABEL_RE.sub("", cleaned).strip(" .")
    size = _lookup(cleaned, GLOVE_SIZE_EN420_MAP, _FOLDED_GLOVE_SIZE_MAP)
    if size is not None:
        return size

    # Fallback for decorated values ("größe 8 (m)", "8-9", "handschuh 10").
    for token in _TOKEN_SPLIT_RE.split(cleaned):
        token = token.strip(" .")
        if not token:
            continue
        size = _lookup(token, GLOVE_SIZE_EN420_MAP, _FOLDED_GLOVE_SIZE_MAP)
        if size is not None:
            return size

    leading_number = _LEADING_NUMBER_RE.search(cleaned)
    if leading_number:
        size = _lookup(
            leading_number.group(0).replace(",", "."),
            GLOVE_SIZE_EN420_MAP,
            _FOLDED_GLOVE_SIZE_MAP,
        )
        if size is not None:
            return size

    return GloveSize.NOT_APPLICABLE


def normalize_color(raw: str | None) -> StandardizedColor:
    """Map German colour text to :class:`StandardizedColor`.

    Compound values (``"transparent/klar"``, ``"blau/weiß"``) resolve to the
    first recognizable segment. Unmappable text yields ``UNKNOWN``.
    """

    if raw is None or not str(raw).strip():
        return StandardizedColor.UNKNOWN

    cleaned = _clean_text(raw)
    for candidate in _candidates(cleaned):
        color = _lookup(candidate, GERMAN_COLOR_MAP, _FOLDED_COLOR_MAP)
        if color is not None:
            return color

    scanned = _keyword_scan(cleaned, GERMAN_COLOR_MAP)
    if scanned is not None:
        return scanned

    return StandardizedColor.UNKNOWN


def normalize_material(raw: str | None) -> MaterialType:
    """Map German material text (``"Nitril"``, ``"Naturlatex"``) to :class:`MaterialType`."""

    if raw is None or not str(raw).strip():
        return MaterialType.NOT_APPLICABLE

    cleaned = _clean_text(raw)
    for candidate in _candidates(cleaned):
        material = _lookup(candidate, GERMAN_MATERIAL_MAP, _FOLDED_MATERIAL_MAP)
        if material is not None:
            return material

    scanned = _keyword_scan(cleaned, GERMAN_MATERIAL_MAP)
    if scanned is not None:
        return scanned

    return MaterialType.NOT_APPLICABLE


def normalize_unit_of_measure(raw: str | None) -> UnitOfMeasure:
    """Map packaging text (``"Karton à 1000 Stk"``) to :class:`UnitOfMeasure`."""

    if raw is None or not str(raw).strip():
        return UnitOfMeasure.NOT_APPLICABLE

    cleaned = _clean_text(raw)
    for candidate in _candidates(cleaned):
        unit = _lookup(candidate, GERMAN_UNIT_MAP, _FOLDED_UNIT_MAP)
        if unit is not None:
            return unit

    scanned = _keyword_scan(cleaned, GERMAN_UNIT_MAP)
    if scanned is not None:
        return scanned

    return UnitOfMeasure.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Numeric normalizers
# ---------------------------------------------------------------------------


def normalize_decimal(
    raw: str | int | float | Decimal | None,
    *,
    percent_as_fraction: bool = True,
) -> Decimal:
    """Parse a German-formatted number into an exact :class:`decimal.Decimal`.

    Handles ``"1.234,56"``, ``"1,234.56"``, ``"19 %"``, ``"2,50 €"`` and
    ``"1.000 Stk"``. Percentages become fractions by default (``"19 %"`` →
    ``Decimal("0.19")``); pass ``percent_as_fraction=False`` to keep ``19``.

    Raises:
        NormalizationError: for ``None``/booleans/empty or unparsable text.
    """

    if raw is None:
        raise NormalizationError("cannot normalize None to Decimal")
    if isinstance(raw, bool):
        raise NormalizationError(f"cannot normalize bool {raw!r} to Decimal")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        # Go through str() so binary floating point noise never leaks in.
        return Decimal(str(raw))

    text = _clean_text(raw)
    if not text:
        raise NormalizationError(f"cannot normalize empty value {raw!r} to Decimal")

    negative = text.startswith("-") or text.startswith("−")
    is_percent = text.endswith("%") or "%" in text

    text = text.replace("%", "").replace("−", "-").replace(" ", "")
    text = _CURRENCY_RE.sub("", text)
    text = _strip_non_numeric(text)
    if not text or text in {"-", ".", ",", "-.", "-,"}:
        raise NormalizationError(f"no numeric content in {raw!r}")

    if "." in text and "," in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if _THOUSANDS_COMMA_RE.fullmatch(text) and not re.fullmatch(r"0,\d{3}", text):
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    elif "." in text and _THOUSANDS_DOT_RE.fullmatch(text):
        text = text.replace(".", "")

    if negative and not text.startswith("-"):
        text = "-" + text

    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded above
        raise NormalizationError(f"cannot parse {raw!r} as Decimal") from exc

    if is_percent and percent_as_fraction:
        value = value / Decimal(100)
    return value


def normalize_percentage(raw: str | int | float | Decimal | None) -> Decimal:
    """Normalize a VAT/margin rate to a fraction (``"19"``/``"19%"`` → ``0.19``)."""

    if raw is None:
        raise NormalizationError("cannot normalize None to a percentage")
    value = normalize_decimal(raw, percent_as_fraction=True)
    has_symbol = "%" in str(raw)
    if not has_symbol and value > Decimal(1):
        value = value / Decimal(100)
    return value


def normalize_pack_size(raw: str | int | float | Decimal | None) -> int | None:
    """Extract the pack content from ``"Karton à 1000 Stk"`` → ``1000``.

    Returns ``None`` when no number is present.
    """

    if raw is None:
        return None
    if isinstance(raw, bool):
        raise NormalizationError(f"cannot normalize bool {raw!r} to pack size")
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, (float, Decimal)):
        value = normalize_decimal(raw)
        if value <= 0:
            return None
        if value != value.to_integral_value():
            raise NormalizationError(f"pack size {raw!r} is not an integer")
        return int(value)

    text = _clean_text(raw)
    if not text:
        return None

    match = _PACK_CONTENT_RE.search(text) or _ANY_NUMBER_RE.search(text)
    if match is None:
        return None

    number = match.group("value") if "value" in match.re.groupindex else match.group(0)
    number = number.replace(" ", "")
    value = normalize_decimal(number)
    if value <= 0:
        return None
    if value != value.to_integral_value():
        raise NormalizationError(f"pack size {raw!r} is not an integer")
    return int(value)


def normalize_quantity(raw: str | int | float | Decimal | None) -> int:
    """Normalize an order/MOQ quantity to a positive integer.

    Raises:
        NormalizationError: when the value is missing, non-numeric, or fractional.
    """

    value = normalize_decimal(raw)
    if value <= 0:
        raise NormalizationError(f"quantity must be positive, got {raw!r}")
    if value != value.to_integral_value():
        raise NormalizationError(f"quantity must be integral, got {raw!r}")
    return int(value)


# ---------------------------------------------------------------------------
# Dictionary-level entry point
# ---------------------------------------------------------------------------

_GLOVE_KEYS: Final = frozenset(
    {"glove_size", "size", "handschuhgroesse", "handschuhgröße", "groesse", "größe"}
)
_COLOR_KEYS: Final = frozenset({"color", "colour", "farbe"})
_MATERIAL_KEYS: Final = frozenset({"material", "material_type", "werkstoff"})
_UNIT_KEYS: Final = frozenset(
    {"unit", "unit_of_measure", "einheit", "mengeneinheit", "verpackungseinheit"}
)
_RATE_KEYS: Final = frozenset(
    {"vat_rate", "vat", "mwst", "mwst_satz", "steuersatz", "tax_rate"}
)
_PACK_SIZE_KEYS: Final = frozenset(
    {"pack_size", "packungsgroesse", "packungsgröße", "inhalt", "content_per_pack"}
)
_QUANTITY_KEYS: Final = frozenset({"quantity", "menge", "qty"})


def normalize_raw_extraction(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a raw extraction dictionary before Pydantic validation.

    Recognized keys (German and English spellings) are replaced in place with
    their canonical enum/``Decimal``/``int`` representation; unknown keys are
    copied through untouched so ``extra="forbid"`` still rejects them.

    Raises:
        NormalizationError: when a numeric field cannot be parsed.
    """

    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        lookup_key = key.lower().strip()
        if value is None:
            normalized[key] = None
        elif lookup_key in _GLOVE_KEYS:
            normalized[key] = normalize_glove_size(value)
        elif lookup_key in _COLOR_KEYS:
            normalized[key] = normalize_color(value)
        elif lookup_key in _MATERIAL_KEYS:
            normalized[key] = normalize_material(value)
        elif lookup_key in _UNIT_KEYS:
            normalized[key] = normalize_unit_of_measure(value)
        elif lookup_key in _RATE_KEYS:
            normalized[key] = normalize_percentage(value)
        elif lookup_key in _PACK_SIZE_KEYS:
            normalized[key] = normalize_pack_size(value)
        elif lookup_key in _QUANTITY_KEYS:
            normalized[key] = normalize_quantity(value)
        else:
            normalized[key] = value
    return normalized
