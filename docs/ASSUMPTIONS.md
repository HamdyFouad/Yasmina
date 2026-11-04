# P0 Inference Notes — Domain Schema Assumptions

Sections 5–8 of `docs/DETAILED_SPEC.md` (field-by-field schemas) are not yet
published. This file records the contract that was inferred for P0.1/P0.2 so it
can be reconciled with the authoritative spec later.

## Conventions

- All domain models: `ConfigDict(strict=True, extra="forbid", validate_assignment=True, validate_default=True)`.
- `Decimal` is the only accepted numeric type (no `int`/`float`/`str` coercion).
- Money: 4 decimal places for unit prices, 2 for totals, `ROUND_HALF_UP`.
- Immutable timestamps are timezone-aware UTC (`AwareDatetime`).
- Enums are stored in PostgreSQL as `VARCHAR` values; membership is enforced by
  Pydantic, not by native PostgreSQL enum types.
- IDs are UUIDv4 generated application-side with `gen_random_uuid()` fallback.

## Inferred field groups

| Entity | Assumed fields |
|---|---|
| `Tender` | tender number, title, client, procedure, source format, CPV codes, publication/deadline, currency, status, VAT rate, items, metadata |
| `TenderItem` | position/lot, short/long text, quantity & unit, glove size, material, colour, thickness (µm), dimensions, pack data, delivery location/date, CPV, alternative flag |
| `SupplierQuote` | supplier identity, quote reference, tender link, status, validity, payment/Incoterms/delivery days, lines, total, raw extraction |
| `SupplierQuoteLine` | line number, description, tender item link, material/colour/size, quantity/unit, unit price, MOQ, volume tiers, pack, GTIN, delivery days |
| `CustomerOffer` | offer number, customer, tender link, status, lines, margin, net/VAT/gross, validity, terms, Ed25519 approval, ERPNext reference, GAEB export path |

## Open questions for spec reconciliation

1. Exact naming of the GAEB/PDF "source format" enum values.
2. Whether supplier quotes are stored per tender item only, or also as free
   catalogue quotes (this schema supports both).
3. Official margin semantics (header-level margin vs. per-line margin).
4. Whether thickness must be stored in µm for bags but mm for gloves.
5. Required approval roles / four-eyes rules for `approved_by`.
6. Embedding dimension and model (currently BGE-M3 at `1024`, configurable).

## Hash-chained audit log

`audit_logs` is append-only (UPDATE/DELETE blocked by trigger) and chained with
SHA-256 over canonical JSON including `id`, `occurred_at`, `event_type`,
`actor`, `entity_type`, `entity_id`, `payload` and `prev_hash`. The audit writer
service (not part of P0) must serialize entries and verify the chain.
