/**
 * Strict domain types mirroring the Pydantic schemas and API serializers.
 * Decimal values are serialized as strings by `model_dump(mode="json")` and by
 * `StoredInvoice.as_dict()`, so money fields are typed as `string`.
 */

export type Currency = 'EUR' | 'USD' | 'CHF'

export type TenderStatus =
  | 'DRAFT'
  | 'PUBLISHED'
  | 'CLOSED'
  | 'IN_EVALUATION'
  | 'AWARDED'
  | 'CANCELLED'
  | 'ARCHIVED'

export type ProcurementProcedure =
  | 'OPEN_PROCEDURE'
  | 'RESTRICTED_PROCEDURE'
  | 'NEGOTIATED_PROCEDURE'
  | 'COMPETITIVE_DIALOGUE'
  | 'DIRECT_AWARD'

export type TenderSourceFormat =
  | 'GAEB_X83'
  | 'GAEB_D83'
  | 'GAEB_X84'
  | 'PDF_DOCLING'
  | 'PORTAL'
  | 'API'
  | 'MANUAL'

export type UnitOfMeasure =
  | 'STUECK'
  | 'PACK'
  | 'KARTON'
  | 'PALETTE'
  | 'ROLLE'
  | 'KILOGRAMM'
  | 'LITER'
  | 'METER'
  | 'QUADRATMETER'
  | 'NOT_APPLICABLE'

export type GloveSize = 'XS' | 'S' | 'M' | 'L' | 'XL' | 'XXL' | 'NOT_APPLICABLE'

export type MaterialType =
  | 'NITRILE'
  | 'LATEX'
  | 'VINYL'
  | 'NEOPRENE'
  | 'BUTYL'
  | 'PVC'
  | 'POLYETHYLENE'
  | 'POLYPROPYLENE'
  | 'PAPER'
  | 'RECYCLED_PAPER'
  | 'COTTON'
  | 'MICROFIBER'
  | 'MIXED'
  | 'NOT_APPLICABLE'

export type StandardizedColor =
  | 'BLUE'
  | 'BLACK'
  | 'YELLOW'
  | 'RED'
  | 'GREEN'
  | 'GREY'
  | 'BROWN'
  | 'ORANGE'
  | 'PINK'
  | 'PURPLE'
  | 'WHITE'
  | 'TRANSPARENT'
  | 'CLEAR'
  | 'NATURAL'
  | 'PRINTED_MULTICOLOR'
  | 'UNKNOWN'

export type InvoiceStatus =
  | 'DRAFT'
  | 'ISSUED'
  | 'PARTIALLY_PAID'
  | 'PAID'
  | 'OVERDUE'
  | 'PARTIALLY_CREDITED'
  | 'CREDITED'
  | 'CANCELLED'

export type CustomerOfferStatus =
  | 'DRAFT'
  | 'PENDING_APPROVAL'
  | 'APPROVED'
  | 'REJECTED'
  | 'SENT'
  | 'ACCEPTED'
  | 'EXPIRED'

export interface TenderItem {
  id: string
  tender_id: string | null
  position_number: string
  lot: string | null
  short_text: string
  long_text: string | null
  quantity: string
  unit: UnitOfMeasure
  glove_size: GloveSize
  material: MaterialType | null
  color: StandardizedColor | null
  thickness_um: string | null
  dimensions: string | null
  pack_size: number | null
  packs_per_carton: number | null
  packaging_description: string | null
  delivery_location: string | null
  requested_delivery_date: string | null
  cpv_code: string | null
  is_alternative_allowed: boolean
  notes: string | null
  source_reference: string | null
  attributes: Record<string, unknown>
}

export interface Tender {
  id: string
  tender_number: string
  title: string
  description: string | null
  client_name: string
  client_contact_email: string | null
  procedure: ProcurementProcedure
  source_format: TenderSourceFormat
  source_reference: string | null
  owner: string | null
  cpv_codes: string[]
  publication_date: string | null
  submission_deadline: string
  currency: Currency
  status: TenderStatus
  vat_rate: string
  items: TenderItem[]
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface CustomerOfferLine {
  id: string
  offer_id: string | null
  position_number: string
  description: string
  tender_item_id: string | null
  supplier_quote_line_id: string | null
  quantity: string
  unit: UnitOfMeasure
  cost_unit_price_net: string
  margin_rate: string
  unit_price_net: string
  total_net: string
  vat_rate: string
}

export interface CustomerOffer {
  id: string
  offer_number: string
  customer_name: string
  customer_contact_email: string | null
  customer_reference: string | null
  tender_id: string | null
  status: CustomerOfferStatus
  currency: Currency
  lines: CustomerOfferLine[]
  margin_rate: string
  total_net: string
  vat_rate: string
  total_vat: string
  total_gross: string
  valid_until: string
  payment_terms: string | null
  delivery_terms: string | null
  ed25519_approval_signature: string | null
  approval_payload_hash: string | null
  approved_by: string | null
  approved_at: string | null
  owner: string | null
  erpnext_quotation_name: string | null
  invoice_artifacts: Record<string, unknown>
  gaeb_export_path: string | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface InvoiceVatSplit {
  rate: string
  taxable_amount: string
  tax_amount: string
}

export interface Invoice {
  id: string
  offer_id: string
  invoice_number: string
  issue_date: string
  due_date: string
  status: InvoiceStatus
  currency: Currency
  total_net: string
  total_vat: string
  total_gross: string
  paid_amount: string
  credited_amount: string
  paid_at: string | null
  credit_note_number: string | null
  vat_breakdown: InvoiceVatSplit[]
  artifacts: Record<string, unknown>
}

export interface InvoiceListResponse {
  invoices: Invoice[]
  count: number
  limit: number
  offset: number
}

export type InvoiceDocumentFormat = 'zugferd' | 'xrechnung'

export interface TenderDocument {
  filename: string | null
  available: boolean
}

export interface TenderListResponse {
  tenders: Tender[]
  count: number
  limit: number
  offset: number
}

export interface TenderDetailResponse {
  tender: Tender
  document: TenderDocument
}

export interface ActivityEvent {
  id: number
  occurred_at: string
  event_type: string
  actor: string
  entity_type: string
  entity_id: string
  payload: Record<string, unknown>
}

export interface ActivityResponse {
  events: ActivityEvent[]
  count: number
}

export interface OpsSummary {
  tenders: number
  offers: number
  pending_approvals: number
  invoices: number
  overdue_invoices: number
  gross_invoiced: string
  gross_paid: string
  currency: Currency
}

export interface InvoiceDetailResponse {
  invoice: Invoice
  events: InvoiceEvent[]
  event_chain_valid: boolean
}

export interface InvoiceEvent {
  id: string
  invoice_id: string
  sequence: number
  event_type: string
  occurred_at: string
  actor: string | null
  amount: string | null
  reference: string | null
  payload: Record<string, unknown>
  prev_hash: string
  entry_hash: string
}

export interface OfferListResponse {
  offers: CustomerOffer[]
  count: number
  limit: number
  offset: number
}

export interface MatchCandidate {
  article_number: string
  description: string
  score: number
  lexical_score: number
  semantic_score: number | null
  matched_attributes: string[]
  reasons: string[]
}

export interface MatchResponse {
  tender_id: string
  matches: Record<string, MatchCandidate[]>
}

export interface PricingResponse {
  offer: CustomerOffer
  warnings: string[]
}

export interface InvoicePaymentRequest {
  amount: string
  reference?: string
}

export interface InvoicePaymentResponse {
  invoice: Invoice
  events: InvoiceEvent[]
}

export interface QuotationSyncCounts {
  pending: number
  processing: number
  succeeded: number
  failed: number
  skipped: number
}

export interface OutboxStatus {
  quotation_sync: QuotationSyncCounts
  total: number
}

export interface CatalogOverride {
  id: string
  supplier_id: string
  supplier_name: string
  article_number: string
  description: string
  material: string | null
  color: string | null
  glove_size: string
  unit: string | null
  unit_price_net: string | null
  pack_size: number | null
  manual_overrides: string[]
  updated_at: string
}

export interface CatalogOverrideListResponse {
  overrides: CatalogOverride[]
  count: number
  limit: number
  offset: number
}

export type CatalogOverrideMode = 'add' | 'replace' | 'clear'

export interface CatalogOverrideRequest {
  supplier_name: string
  article_number: string
  fields: string[]
  mode: CatalogOverrideMode
}
