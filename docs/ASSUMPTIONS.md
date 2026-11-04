# P0/P1 Inference Notes — Domain & Service Assumptions

Sections 5–8 of `docs/DETAILED_SPEC.md` (field-by-field schemas and pricing
rules) are not yet published. This file records the contract inferred for the
P0 foundation and the P1 parsers/pricing/matching services so it can be
reconciled with the authoritative spec later.

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

## Service assumptions (P1)

- **Pricing:** volume tier price applies to the whole line quantity; the highest
  qualifying tier wins. MOQ/pack rounding is opt-in (`round_to_moq=True`) because
  the customer-facing quantity usually stays at the tendered amount. Margin is a
  fraction (0.15 = 15 %), unit prices round half-up to 4 dp, totals to 2 dp.
- **Matching:** stage 1 rejects only when both sides expose a conflicting
  material/colour/glove size/unit — missing catalogue attributes are not
  penalized. Stage 2 uses 40 % lexical / 60 % embedding cosine by default, with
  a small exact-attribute bonus; callers supply query embeddings from pgvector.
- **GAEB parsing:** full OZ is used as `position_number`; missing quantities,
  unknown units and duplicate positions are reported as warnings. Missing dates
  fall back to `now + 30 days`; missing clients use a placeholder.
- **GAEB writing:** only approved offers with an Ed25519 signature and payload
  hash may produce `.x84`. Position prices are matched by full OZ, falling back
  to the leaf OZ.
- **GAEB validation:** penny reconciliation compares item and document totals
  with a one-cent threshold. XSD validation requires an explicit `xsd_dir`; the
  official schemas are not bundled with the project.
- **PDF parsing:** Docling table rows are mapped through German header aliases;
  rows without a position, description or positive quantity are skipped with a
  warning.

## Agent and approval assumptions (P1.2/P1.3)

- The LangGraph workflow is single-pass and sync: enrich → match → price →
  approval gate → X84 export. A resumed pass can carry a signed offer and skip
  re-pricing. Statuses are `pending_approval`, `approved`, `approval_invalid`,
  `exported` and `failed`.
- DeepSeek is used only to fill missing material/colour/size/thickness/pack
  hints. Its output is normalized before merge; LLM failures are warnings, not
  hard failures.
- The approval payload is canonical JSON (sorted keys, compact separators,
  UTF-8) covering the offer, its lines, totals, approver and approval timestamp.
  The Ed25519 signature signs those bytes; the stored SHA-256 hash is a separate
  integrity value. Private keys never belong on the application server.
- The X84 writer can verify against an `ApprovalService`; the orchestration layer
  always passes the configured verifier.

## Open questions for spec reconciliation

1. Exact naming of the GAEB/PDF "source format" enum values.
2. Whether supplier quotes are stored per tender item only, or also as free
   catalogue quotes (this schema supports both).
3. Official margin semantics (header-level margin vs. per-line margin).
4. Whether thickness must be stored in µm for bags but mm for gloves.
5. Required approval roles / four-eyes rules for `approved_by`.
6. Embedding dimension and model (currently BGE-M3 at `1024`, configurable).

## Hash-chained audit log

`audit_logs` is append-only (UPDATE/DELETE/TRUNCATE blocked by triggers) and
chained with SHA-256 over canonical JSON including `id`, `occurred_at`,
`event_type`, `actor`, `entity_type`, `entity_id`, `payload` and `prev_hash`.
The audit writer service (not yet implemented) must serialize entries and verify
the chain.
