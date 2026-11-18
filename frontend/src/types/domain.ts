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
  thickness_mm: string | null
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

export interface InvoiceLine {
  line_id: number
  name: string
  quantity: string
  unit: UnitOfMeasure
  unit_price: string
  line_total: string
  vat_rate: string
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
  buyer_email: string | null
  vat_breakdown: InvoiceVatSplit[]
  lines: InvoiceLine[]
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

export interface UploadTenderResponse {
  tender_id: string
  tender: Tender
  object_name: string
  warnings: string[]
}

export interface TenderOwnerResponse {
  tender: Tender
}

export interface TenderWorkflowResponse {
  status: string
  offer: CustomerOffer | null
  triage: Record<string, unknown> | null
  errors: string[]
  warnings: string[]
  exported_path: string | null
}

export type SupplierQuoteSource = 'DASHBOARD' | 'N8N'

export type SupplierQuoteStatus =
  | 'RECEIVED'
  | 'NORMALIZED'
  | 'VALIDATED'
  | 'ACCEPTED'
  | 'REJECTED'
  | 'EXPIRED'

export interface SupplierQuoteTier {
  min_quantity: string
  unit_price_net: string
}

export interface SupplierQuoteLine {
  id: string
  supplier_quote_id: string | null
  line_number: number
  description: string
  tender_item_id: string | null
  material: MaterialType | null
  color: StandardizedColor | null
  glove_size: GloveSize
  quantity: string
  unit: UnitOfMeasure
  unit_price_net: string
  moq: string | null
  tiers: SupplierQuoteTier[]
  pack_size: number | null
  gtin: string | null
  delivery_days: number | null
  notes: string | null
  attributes: Record<string, unknown>
  is_accepted: boolean
  accepted_at: string | null
}

export interface SupplierQuote {
  id: string
  supplier_id: string | null
  supplier_name: string
  supplier_contact_email: string | null
  quote_reference: string
  tender_id: string | null
  status: SupplierQuoteStatus
  currency: Currency
  valid_until: string | null
  payment_terms: string | null
  incoterms: string | null
  delivery_days: number | null
  lines: SupplierQuoteLine[]
  total_net: string | null
  accepted_at: string | null
  accepted_by: string | null
  source_document: string | null
  raw_extraction: Record<string, unknown>
  created_at: string
  updated_at: string
  source: SupplierQuoteSource
  line_count: number
}

export interface SupplierQuoteListResponse {
  quotes: SupplierQuote[]
  count: number
  limit: number
  offset: number
}

export interface SupplierQuoteResponse {
  quote: SupplierQuote
}

export type PurchaseOrderStatus = 'DRAFT' | 'SENT' | 'CONFIRMED' | 'DELIVERED' | 'CANCELLED'

export type SupplierInvoiceStatus =
  | 'PENDING'
  | 'MATCHED'
  | 'EXCEPTION'
  | 'APPROVED'
  | 'REJECTED'

export interface PurchaseOrderLine {
  id: string
  purchase_order_id: string
  supplier_quote_line_id: string
  line_number: number
  description: string
  tender_item_id: string | null
  quantity: string
  unit: UnitOfMeasure
  unit_price_net: string
  total_net: string
  received_quantity: string
}

export interface PurchaseOrder {
  id: string
  po_number: string
  supplier_id: string
  supplier_name: string
  supplier_contact_email: string | null
  tender_id: string | null
  supplier_quote_id: string | null
  customer_offer_id: string | null
  status: PurchaseOrderStatus
  currency: Currency
  order_date: string
  expected_delivery_date: string | null
  sent_at: string | null
  confirmed_at: string | null
  delivered_at: string | null
  cancelled_at: string | null
  total_net: string
  notes: string | null
  requested_by: string
  lines: PurchaseOrderLine[]
  created_at: string
  updated_at: string
}

export interface PurchaseOrdersResponse {
  purchase_orders: PurchaseOrder[]
  count: number
  limit: number
  offset: number
}

export interface PurchaseOrderResponse {
  purchase_order: PurchaseOrder
}

export interface PurchaseOrderCreateRequest {
  supplier_quote_id: string
  customer_offer_id?: string | null
  expected_delivery_date?: string | null
  notes?: string | null
}

export interface SupplierInvoiceLine {
  id: string
  supplier_invoice_id: string
  purchase_order_line_id: string
  description: string
  quantity: string
  unit: UnitOfMeasure
  unit_price_net: string
  total_net: string
}

export interface SupplierInvoice {
  id: string
  purchase_order_id: string
  po_number: string
  supplier_id: string
  supplier_name: string
  invoice_number: string
  invoice_date: string
  delivery_date: string | null
  due_date: string | null
  currency: Currency
  total_net: string
  total_vat: string
  total_gross: string
  status: SupplierInvoiceStatus
  exception_codes: string[]
  variance_net: string
  match_details: Record<string, unknown>
  matched_at: string | null
  notes: string | null
  received_by: string
  lines: SupplierInvoiceLine[]
  created_at: string
  updated_at: string
}

export interface SupplierInvoicesResponse {
  supplier_invoices: SupplierInvoice[]
  count: number
  limit: number
  offset: number
}

export interface SupplierInvoiceResponse {
  supplier_invoice: SupplierInvoice
}

export interface SupplierInvoiceLineRequest {
  purchase_order_line_id: string
  quantity: string
  unit_price_net: string
  description?: string | null
}

export interface SupplierInvoiceRequest {
  purchase_order_id: string
  invoice_number: string
  invoice_date: string
  delivery_date?: string | null
  due_date?: string | null
  total_net: string
  vat_rate?: string
  notes?: string | null
  lines: SupplierInvoiceLineRequest[]
}

export interface MarginSummary {
  offer_id: string
  offer_number: string
  customer_name: string
  currency: Currency
  quoted_revenue_net: string
  quoted_cost_net: string
  quoted_margin_net: string
  quoted_margin_rate: string | null
  actual_cost_net: string
  actual_margin_net: string | null
  actual_margin_rate: string | null
  margin_variance_net: string | null
  margin_variance_rate: string | null
  invoice_count: number
  purchase_order_count: number
  exception_count: number
  exception_codes: string[]
  coverage_rate: string | null
}

export interface MarginSummariesResponse {
  margins: MarginSummary[]
  count: number
  limit: number
  offset: number
}

export interface MarginSummaryResponse {
  margin: MarginSummary
}

export interface AcceptedSupplierQuotesResponse {
  quotes: SupplierQuote[]
  count: number
}

export interface ProcurementHealth {
  purchase_orders: number
  exceptions: number
  today: string
}

export interface PipelineStage {
  status: string
  count: number
  total_net: string
}

export interface WinLoss {
  won_count: number
  lost_count: number
  won_value: string
  lost_value: string
  win_rate: string | null
}

export interface ConversionMetrics {
  total_tenders: number
  tenders_with_offers: number
  tenders_with_accepted_offers: number
  offer_conversion_rate: string | null
  tender_coverage_rate: string | null
  tender_win_rate: string | null
}

export interface ReportingSummary {
  kpis: {
    tenders: number
    offers: number
    pipeline_value: string
    won_offers: number
    win_rate: string | null
    gross_invoiced: string
    outstanding: string
    overdue_amount: string
    currency: Currency
  }
  pipeline: PipelineStage[]
  win_loss: WinLoss
  conversion: ConversionMetrics
}

export interface MarginBreakdown {
  dimension: 'customer' | 'tender' | 'supplier'
  label: string
  offer_count: number
  revenue_net: string
  cost_net: string
  margin_net: string
  margin_rate: string | null
}

export interface ReportingMarginsResponse {
  customers: MarginBreakdown[]
  tenders: MarginBreakdown[]
  suppliers: MarginBreakdown[]
}

export interface AgingBucket {
  label: string
  invoice_count: number
  total_gross: string
  paid_amount: string
  outstanding: string
}

export interface AgingReport {
  buckets: AgingBucket[]
  invoice_count: number
  paid_total: string
  overdue_total: string
  outstanding_total: string
  currency: Currency
}

export interface NotificationAlert {
  id: string
  severity: 'critical' | 'warning' | 'info'
  title: string
  message: string
  count: number
  href: string
}

export interface NotificationsResponse {
  alerts: NotificationAlert[]
  count: number
}

export interface AuditLogEntry {
  id: number
  occurred_at: string
  event_type: string
  actor: string
  entity_type: string
  entity_id: string
  payload: Record<string, unknown>
  prev_hash: string | null
  entry_hash: string
  signature: string | null
}

export interface AuditLogsResponse {
  logs: AuditLogEntry[]
  count: number
  limit: number
  offset: number
}

export interface AuditEventTypesResponse {
  event_types: string[]
}

export type AuditExportFormat = 'csv' | 'xlsx'

export interface ManualSupplierQuoteLineRequest {
  line_number: number
  description: string
  tender_item_id: string
  quantity: string
  unit: UnitOfMeasure
  unit_price_net: string
  moq?: string | null
  pack_size?: number | null
  delivery_days?: number | null
  notes?: string | null
}

export interface ManualSupplierQuoteRequest {
  tender_id: string
  supplier_name: string
  supplier_contact_email?: string | null
  quote_reference: string
  valid_until?: string | null
  payment_terms?: string | null
  incoterms?: string | null
  delivery_days?: number | null
  lines: ManualSupplierQuoteLineRequest[]
}

export interface RfqSupplier {
  supplier_id?: string | null
  name: string
  email: string
  contact_name?: string | null
}

export interface RfqDispatchRequest {
  tender_id: string
  suppliers: RfqSupplier[]
  response_deadline?: string | null
  notes?: string | null
}

export interface RfqDispatchResponse {
  status: string
  event_id: string
  idempotency_key: string
  webhook_status: number
  n8n_response: Record<string, unknown> | string | null
  replayed: boolean
  audit_recorded?: boolean
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

export interface CreditNoteLine {
  description: string
  net_amount: string
  vat_rate: string
  tax_amount: string
  gross_amount: string
}

export interface CreditNote {
  id: string
  invoice_id: string
  credit_note_number: string
  issue_date: string
  status: string
  reason: string
  total_net: string
  total_vat: string
  total_gross: string
  vat_breakdown: InvoiceVatSplit[]
  lines: CreditNoteLine[]
  artifacts: Record<string, InvoiceArtifact>
}

export interface InvoiceCancelRequest {
  reason: string
}

export interface InvoiceCancelResponse {
  invoice: Invoice
  events: InvoiceEvent[]
}

export interface InvoiceEmailRequest {
  recipient?: string
}

export interface InvoiceEmailDelivery {
  recipient: string
  artifacts: string[]
}

export interface InvoiceEmailResponse {
  invoice: Invoice
  events: InvoiceEvent[]
  delivery: InvoiceEmailDelivery
}

export interface InvoiceCreditRequest {
  amount?: string
  reason: string
  issue_date?: string
}

export interface InvoiceCreditResponse {
  credit_note: CreditNote
  invoice: Invoice | null
  events: InvoiceEvent[]
}

export interface InvoiceCreditNotesResponse {
  invoice_id: string
  credit_notes: CreditNote[]
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

export type QuotationSyncStatus = 'pending' | 'processing' | 'succeeded' | 'failed' | 'skipped'

export interface QuotationSyncJob {
  id: string
  offer_id: string
  offer_number: string
  status: QuotationSyncStatus
  attempts: number
  max_attempts: number
  available_at: string
  quotation_name: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface OutboxJobsResponse {
  jobs: QuotationSyncJob[]
  total: number
  limit: number
  offset: number
}

export interface OutboxRetryResponse {
  job: QuotationSyncJob
}

export type WorkerRunType = 'scrape' | 'ingestion'
export type WorkerRunStatus = 'running' | 'succeeded' | 'failed' | 'skipped' | 'cancelled'

export interface WorkerRun {
  id: string
  run_type: WorkerRunType
  resource: string
  status: WorkerRunStatus
  worker_id: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  items_processed: number
  errors_count: number
  error: string | null
  details: Record<string, unknown>
}

export interface WorkerRunsResponse {
  runs: WorkerRun[]
  count: number
  limit: number
  offset: number
}

export type IngestionJobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'skipped'

export interface IngestionJob {
  id: string
  tender_id: string
  tender_number: string | null
  tender_title: string | null
  portal_name: string
  status: IngestionJobStatus
  attempts: number
  max_attempts: number
  quarantined: boolean
  quarantine_reason: string | null
  last_error: string | null
  attachment_count: number
  available_at: string | null
  locked_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface IngestionJobsResponse {
  jobs: IngestionJob[]
  count: number
  limit: number
  offset: number
  status_counts: Record<IngestionJobStatus, number>
  quarantine_count: number
}

export interface PortalTargetSummary {
  id: string
  name: string
  engine: string
  enabled: boolean
  api_feed: boolean
}

export interface PortalsResponse {
  portals: PortalTargetSummary[]
  count: number
}

export interface ProbeIssue {
  url: string
  code: string
  message: string
}

export interface ProbeReport {
  read_only: boolean
  probed_at: string
  elapsed_seconds: number
  portal: {
    id: string
    name: string
    engine: string
    start_url: string
    enabled: boolean
    api_feed: boolean
  }
  max_pages: number
  pages_fetched: number
  notices_discovered: number
  notices_parsed: number
  skipped: {
    cpv: number
    invalid: number
    stale: number
  }
  details_fetched: number
  detail_pages_parsed: number
  positions_parsed: number
  notices_with_positions: number
  zero_positions: boolean
  errors: string[]
  issues: ProbeIssue[]
  issues_truncated: number
  warnings: string[]
  zero_results: boolean
}

export interface WorkerCommand {
  id: string
  command_type: 'scrape' | 'ingestion'
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  portal: string | null
  max_pages: number | null
  batch_limit: number | null
  pid: number | null
  requested_by: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  details: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface WorkerCommandsResponse {
  commands: WorkerCommand[]
  count: number
  limit: number
  offset: number
}

export interface WorkerCommandResponse {
  accepted: boolean
  action: 'scrape' | 'ingestion'
  command: string[]
  pid: number
  worker_command: WorkerCommand
}

export interface ReleaseStaleLocksResponse {
  released: number
  older_than_seconds: number
}

export interface SupplierContact {
  id: string
  name: string
  role: string | null
  email: string | null
  phone: string | null
  is_primary: boolean
}

export interface Supplier {
  id: string
  name: string
  legal_name: string | null
  erpnext_supplier_id: string | null
  email: string | null
  phone: string | null
  street: string | null
  postal_code: string | null
  city: string | null
  country_code: string
  payment_terms: string | null
  delivery_days: number | null
  notes: string | null
  is_active: boolean
  contacts: SupplierContact[]
  primary_contact_name: string | null
  primary_contact_email: string | null
  contact_count: number
  created_at: string
  updated_at: string
}

export interface SuppliersResponse {
  suppliers: Supplier[]
  count: number
  limit: number
  offset: number
}

export interface Customer {
  id: string
  name: string
  legal_name: string | null
  street: string
  postal_code: string
  city: string
  country_code: string
  vat_id: string | null
  leitweg_id: string | null
  payment_terms: string | null
  contact_name: string | null
  contact_email: string | null
  phone: string | null
  notes: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface CustomersResponse {
  customers: Customer[]
  count: number
  limit: number
  offset: number
}

export interface OfferVatSplit {
  rate: string
  taxable_amount: string
  tax_amount: string
}

export interface OfferDetailResponse {
  offer: CustomerOffer
  vat_breakdown: OfferVatSplit[]
}

export interface ApproveOfferResponse {
  offer: CustomerOffer
  artifacts: Record<string, InvoiceArtifact> | null
  erpnext: {
    status: string
    job_id?: string
    quotation_name?: string
    created?: boolean
    reason?: string
  }
  created: boolean
}

export interface InvoiceBuyerPayload {
  street: string
  city: string
  postal_code: string
  country_code?: string
  vat_id?: string | null
  email?: string | null
}

export interface InvoiceRequestPayload {
  buyer: InvoiceBuyerPayload
  buyer_reference: string
  due_date: string
  delivery_date?: string | null
  iban?: string | null
  payment_terms?: string | null
}

export interface InvoiceArtifact {
  object_name: string
  download_url: string
  media_type: string
}

export interface ApproveOfferPayload {
  offer: CustomerOffer
  invoice?: InvoiceRequestPayload
}