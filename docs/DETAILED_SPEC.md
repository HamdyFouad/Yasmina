# Project Yasmina — Section 4: Domain Models, Normalization, & Enums Specification
*(File Target: `docs/DETAILED_SPEC.md` — Referenced by root `AGENTS.md`)*

---

## 4.0 Architectural Separation & Pre-Validation Normalization Layer

### 1. Document Separation (`AGENTS.md` vs. `docs/DETAILED_SPEC.md`)
To optimize context-window efficiency for **OpenCode / DeepSeek Harness**:
- **`AGENTS.md` (Root Directory):** Contains concise operational rules, build/test commands, directory pointers, and strict coding guardrails (< 150 lines).
- **`docs/DETAILED_SPEC.md` (This Document):** Serves as the authoritative engineering reference containing full schemas, enums, SQL filter rules, and cryptographic specifications.

### 2. Mandatory Pre-Validation Normalization Layer (`src/parsers/normalizers.py`)
Because all domain schemas enforce `ConfigDict(strict=True, extra="forbid")`, passing raw German strings extracted by `Docling`, `pygaeb`, or an LLM (e.g., `"Nitril"`, `"Gr. 8"`, `"19%"`, `"transparent/klar"`, `"Karton à 1000 Stk"`) directly into Pydantic constructors will immediately raise a `ValidationError`.

**Strict Architectural Rule:** Parsers (`pdf_parser.py`, `gaeb_parser.py`) and LLM structured-output nodes **must never instantiate domain models directly from raw text**. All raw extraction dictionaries must pass through `src/parsers/normalizers.py` first:

```python
# Conceptual flow in src/parsers/normalizers.py
from decimal import Decimal
from src.models.enums import GloveSize, MaterialType, StandardizedColor, UnitOfMeasure

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

def normalize_glove_size(raw: str | int | float | None) -> GloveSize:
    if raw is None:
        return GloveSize.NOT_APPLICABLE
    cleaned = str(raw).lower().replace("größe", "").replace("gr.", "").replace(",", ".").strip()
    return GLOVE_SIZE_EN420_MAP.get(cleaned, GloveSize.NOT_APPLICABLE)
```

### 3. Implementation Notes (Authoritative)
- The production implementation lives in `src/parsers/normalizers.py` and extends the conceptual excerpt above with:
  - umlaut/ß folding (`grün` / `gruen`, `weiß` / `weiss`, `groß` / `gross`);
  - compound colour handling (`"transparent/klar"`, `"blau/weiß"` → first resolvable token);
  - `GERMAN_MATERIAL_MAP` (`"Nitril"` → `MaterialType.NITRILE`) and `GERMAN_UNIT_MAP` (`"Karton à 1000 Stk"` → `UnitOfMeasure.KARTON`);
  - deterministic `decimal.Decimal` parsing for German number formats (`"1.234,56"`), percentages (`"19%"` → `0.19`) and pack contents (`"à 1000 Stk"` → `1000`).
- Normalizers are **pure functions**: no I/O, no network, no LLM calls. Unmappable values raise `NormalizationError` (a `ValueError`) instead of silently producing defaults, except where the domain explicitly defines a fallback (`GloveSize.NOT_APPLICABLE`, `StandardizedColor.UNKNOWN`, `MaterialType.NOT_APPLICABLE`, `UnitOfMeasure.NOT_APPLICABLE`).
- Every raw extraction dictionary is passed through `normalize_raw_extraction()` before Pydantic validation; parsers and LLM nodes never instantiate domain models from raw text.

---
