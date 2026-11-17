import * as React from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, Calculator, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { apiErrorMessage } from '@/lib/api-error'
import { unitLabel } from '@/lib/labels'
import { priceTender, runTenderWorkflow } from '@/lib/offers-api'
import { fetchSupplierQuotes } from '@/lib/sourcing-api'
import { formatCurrency, formatPercent, formatQuantity } from '@/lib/utils'
import type {
  CustomerOffer,
  OfferVatSplit,
  SupplierQuote,
  SupplierQuoteLine,
  Tender,
  TenderWorkflowResponse,
} from '@/types/domain'

const VAT_OPTIONS = [
  { value: '0.19', label: '19 %' },
  { value: '0.07', label: '7 %' },
  { value: '0.00', label: '0 %' },
] as const

interface OfferForm {
  offerNumber: string
  customerName: string
  validUntil: string
  marginPercent: string
  vatRate: string
  freight: string
  palletUnit: string
  palletCount: string
}

interface ParsedCost {
  value: string
  amount: number
}

interface PricePayload {
  supplier_quotes: Record<string, unknown>[]
  offer_request: Record<string, unknown>
}

interface FormErrors {
  offerNumber?: string
  customerName?: string
  validUntil?: string
  margin?: string
  freight?: string
  palletUnit?: string
  palletCount?: string
  costs: Record<string, string>
}

const FIELD_ORDER = [
  'offerNumber',
  'customerName',
  'validUntil',
  'margin',
  'freight',
  'palletUnit',
  'palletCount',
] as const

function isoDateAfterDays(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = React.useState(value)
  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}

function parseDecimal(
  raw: string,
  decimals: number,
  { allowZero }: { allowZero: boolean },
): { value: string; amount: number } | null {
  const normalized = raw.trim().replace(',', '.')
  const pattern = new RegExp(`^\\d+(?:\\.\\d{1,${decimals}})?$`)
  if (!pattern.test(normalized)) {
    return null
  }
  const amount = Number.parseFloat(normalized)
  if (!Number.isFinite(amount) || amount < 0 || (!allowZero && amount === 0)) {
    return null
  }
  return { value: amount.toFixed(decimals), amount }
}

function parseInteger(raw: string): number | null {
  const normalized = raw.trim()
  if (!/^\d+$/.test(normalized)) {
    return null
  }
  const value = Number.parseInt(normalized, 10)
  return Number.isFinite(value) && value >= 0 && value <= 10_000 ? value : null
}

function formatVatBreakdown(offer: CustomerOffer): OfferVatSplit[] {
  const taxable = new Map<string, number>()
  for (const line of offer.lines) {
    taxable.set(line.vat_rate, (taxable.get(line.vat_rate) ?? 0) + Number.parseFloat(line.total_net))
  }
  return [...taxable.entries()]
    .sort(([left], [right]) => Number.parseFloat(left) - Number.parseFloat(right))
    .map(([rate, base]) => ({
      rate,
      taxable_amount: base.toFixed(2),
      tax_amount: (base * Number.parseFloat(rate)).toFixed(2),
    }))
}

function bestQuoteLine(
  lines: SupplierQuoteLine[] | undefined,
): SupplierQuoteLine | undefined {
  if (lines === undefined || lines.length === 0) {
    return undefined
  }
  return [...lines].sort(
    (left, right) => Number.parseFloat(left.unit_price_net) - Number.parseFloat(right.unit_price_net),
  )[0]
}

function quoteLineMap(quote: SupplierQuote | undefined): Map<string, SupplierQuoteLine> {
  const result = new Map<string, SupplierQuoteLine>()
  if (quote === undefined) {
    return result
  }
  const grouped = new Map<string, SupplierQuoteLine[]>()
  for (const line of quote.lines) {
    if (line.tender_item_id === null) {
      continue
    }
    grouped.set(line.tender_item_id, [...(grouped.get(line.tender_item_id) ?? []), line])
  }
  for (const [itemId, lines] of grouped) {
    const best = bestQuoteLine(lines)
    if (best !== undefined) {
      result.set(itemId, best)
    }
  }
  return result
}

function quoteToPayload(quote: SupplierQuote): Record<string, unknown> {
  return {
    id: quote.id,
    supplier_id: quote.supplier_id,
    supplier_name: quote.supplier_name,
    supplier_contact_email: quote.supplier_contact_email,
    quote_reference: quote.quote_reference,
    tender_id: quote.tender_id,
    status: quote.status,
    currency: quote.currency,
    valid_until: quote.valid_until,
    payment_terms: quote.payment_terms,
    incoterms: quote.incoterms,
    delivery_days: quote.delivery_days,
    lines: quote.lines.map((line) => ({
      id: line.id,
      supplier_quote_id: line.supplier_quote_id,
      line_number: line.line_number,
      description: line.description,
      tender_item_id: line.tender_item_id,
      material: line.material,
      color: line.color,
      glove_size: line.glove_size,
      quantity: line.quantity,
      unit: line.unit,
      unit_price_net: line.unit_price_net,
      moq: line.moq,
      tiers: line.tiers.map((tier) => ({
        min_quantity: tier.min_quantity,
        unit_price_net: tier.unit_price_net,
      })),
      pack_size: line.pack_size,
      gtin: line.gtin,
      delivery_days: line.delivery_days,
      notes: line.notes,
      attributes: line.attributes,
    })),
    total_net: quote.total_net,
    source_document: quote.source_document,
    raw_extraction: quote.raw_extraction,
    created_at: quote.created_at,
    updated_at: quote.updated_at,
  }
}

function FieldShell({
  id,
  label,
  hint,
  error,
  children,
  required = false,
}: {
  id: string
  label: string
  hint: string
  error?: string | undefined
  children: (props: { describedBy: string; invalid: boolean | undefined }) => React.ReactNode
  required?: boolean
}) {
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </Label>
      <p id={hintId} className="text-xs text-muted-foreground">
        {hint}
      </p>
      {children({
        describedBy: error !== undefined ? `${hintId} ${errorId}` : hintId,
        invalid: error !== undefined ? true : undefined,
      })}
      {error !== undefined ? (
        <p id={errorId} className="text-xs text-destructive">
          <span aria-hidden="true">! </span>
          {error}
        </p>
      ) : null}
    </div>
  )
}

function OfferSuccess({
  offer,
  onClose,
}: {
  offer: CustomerOffer
  onClose: () => void
}) {
  const navigate = useNavigate()
  return (
    <div className="space-y-4">
      <div
        role="status"
        className="flex items-start gap-3 rounded-md border border-success/30 bg-success/5 p-3"
      >
        <Calculator className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium">Offer {offer.offer_number} created</p>
          <p className="text-xs text-muted-foreground">
            The offer is handed off as a draft for the four-eyes approval console.
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-3 gap-2 rounded-md border border-border bg-muted/40 p-3 text-sm">
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">Net</dt>
          <dd>{formatCurrency(offer.total_net, offer.currency)}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">VAT</dt>
          <dd>{formatCurrency(offer.total_vat, offer.currency)}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">Gross</dt>
          <dd className="font-semibold">{formatCurrency(offer.total_gross, offer.currency)}</dd>
        </div>
      </dl>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Close
        </Button>
        <Button type="button" onClick={() => navigate(`/offers?offer=${offer.id}`)}>
          Open approval console
          <ArrowRight />
        </Button>
      </DialogFooter>
    </div>
  )
}

export function OfferCreationDialog({
  tender,
  onClose,
}: {
  tender: Tender
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const formRef = React.useRef<HTMLFormElement>(null)
  const [attempted, setAttempted] = React.useState(false)
  const [touched, setTouched] = React.useState<ReadonlySet<string>>(new Set())
  const [created, setCreated] = React.useState<CustomerOffer | null>(null)
  const [workflowError, setWorkflowError] = React.useState<string | null>(null)
  const [form, setForm] = React.useState<OfferForm>(() => ({
    offerNumber: `AN-${tender.tender_number}-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}`,
    customerName: tender.client_name,
    validUntil: isoDateAfterDays(30),
    marginPercent: '15',
    vatRate: String(tender.vat_rate),
    freight: '0.00',
    palletUnit: '0.00',
    palletCount: '0',
  }))
  const [costs, setCosts] = React.useState<Record<string, string>>(() =>
    Object.fromEntries(tender.items.map((item) => [item.id, ''])),
  )
  const [selectedQuoteId, setSelectedQuoteId] = React.useState('manual')
  const quotes = useQuery({
    queryKey: ['supplier-quotes', tender.id],
    queryFn: () => fetchSupplierQuotes(tender.id, { limit: 50 }),
  })
  const selectedQuote = quotes.data?.quotes.find((quote) => quote.id === selectedQuoteId)
  const quoteLines = React.useMemo(() => quoteLineMap(selectedQuote), [selectedQuote])
  const missingItems = tender.items.filter((item) => !quoteLines.has(item.id))
  const coveredCount = tender.items.length - missingItems.length

  const parsedCosts = React.useMemo(() => {
    const result: Record<string, ParsedCost> = {}
    const errors: Record<string, string> = {}
    for (const item of tender.items) {
      const quoteLine = quoteLines.get(item.id)
      const parsed = quoteLine
        ? parseDecimal(quoteLine.unit_price_net, 4, { allowZero: true })
        : parseDecimal(costs[item.id] ?? '', 4, { allowZero: false })
      if (parsed === null) {
        errors[item.id] = quoteLine
          ? 'The quote line has an invalid unit price.'
          : 'Enter a positive supplier cost with up to four decimals.'
      } else {
        result[item.id] = parsed
      }
    }
    return { result, errors }
  }, [costs, quoteLines, tender.items])

  const margin = parseDecimal(form.marginPercent, 2, { allowZero: true })
  const freight = parseDecimal(form.freight, 2, { allowZero: true })
  const palletUnit = parseDecimal(form.palletUnit, 2, { allowZero: true })
  const palletCount = parseInteger(form.palletCount)
  const validDate =
    /^\d{4}-\d{2}-\d{2}$/.test(form.validUntil) &&
    !Number.isNaN(Date.parse(`${form.validUntil}T00:00:00`))

  const errors: FormErrors = {
    costs: parsedCosts.errors,
    ...(form.offerNumber.trim().length === 0
      ? { offerNumber: 'Enter an offer number.' }
      : form.offerNumber.trim().length > 128
        ? { offerNumber: 'The offer number must be 128 characters or fewer.' }
        : {}),
    ...(form.customerName.trim().length === 0
      ? { customerName: 'Enter the customer name.' }
      : form.customerName.trim().length > 256
        ? { customerName: 'The customer name must be 256 characters or fewer.' }
        : {}),
    ...(!validDate ? { validUntil: 'Choose a valid validity date.' } : {}),
    ...(margin === null
      ? { margin: 'Enter a margin between 0 and 500 % with up to two decimals.' }
      : margin.amount > 500
        ? { margin: 'The margin must be 500 % or less.' }
        : {}),
    ...(freight === null ? { freight: 'Enter a freight amount of 0.00 or more.' } : {}),
    ...(palletUnit === null ? { palletUnit: 'Enter a pallet unit price of 0.00 or more.' } : {}),
    ...(palletCount === null ? { palletCount: 'Enter a whole pallet count between 0 and 10000.' } : {}),
  }

  const hasErrors =
    FIELD_ORDER.some((field) => errors[field] !== undefined) ||
    Object.keys(errors.costs).length > 0
  const marginValue = margin?.value ?? null
  const freightValue = freight?.value ?? null
  const palletUnitValue = palletUnit?.value ?? null
  const payload = React.useMemo<PricePayload | null>(() => {
    if (
      hasErrors ||
      marginValue === null ||
      freightValue === null ||
      palletUnitValue === null ||
      palletCount === null
    ) {
      return null
    }
    const manualLines = (items: Tender['items']): Record<string, unknown>[] =>
      items.map((item, index) => {
        const cost = parsedCosts.result[item.id]
        if (cost === undefined) {
          throw new Error('validated cost missing')
        }
        return {
          line_number: index + 1,
          description: item.short_text,
          tender_item_id: item.id,
          quantity: item.quantity,
          unit: item.unit,
          unit_price_net: cost.value,
        }
      })

    const supplierQuotes: Record<string, unknown>[] = []
    if (selectedQuote !== undefined) {
      supplierQuotes.push(quoteToPayload(selectedQuote))
      const missing = tender.items.filter((item) => !quoteLines.has(item.id))
      if (missing.length > 0) {
        supplierQuotes.push({
          supplier_name: 'Manual fallback quote',
          quote_reference: `FALLBACK-${form.offerNumber}`.slice(0, 128),
          tender_id: tender.id,
          currency: tender.currency,
          raw_extraction: { source: 'dashboard' },
          lines: manualLines(missing),
        })
      }
    } else {
      supplierQuotes.push({
        supplier_name: 'Manual supplier quote',
        quote_reference: `MAN-${form.offerNumber}`.slice(0, 128),
        tender_id: tender.id,
        currency: tender.currency,
        raw_extraction: { source: 'dashboard' },
        lines: manualLines(tender.items),
      })
    }

    return {
      supplier_quotes: supplierQuotes,
      offer_request: {
        customer_name: form.customerName.trim(),
        offer_number: form.offerNumber.trim(),
        valid_until: form.validUntil,
        margin_rate: (Number.parseFloat(marginValue) / 100).toFixed(4),
        vat_rate: form.vatRate,
        ...(tender.client_contact_email !== null
          ? { customer_contact_email: tender.client_contact_email }
          : {}),
        freight_flat_net: freightValue,
        pallet_unit_net: palletUnitValue,
        pallet_count: palletCount,
      },
    }
  }, [
    form.customerName,
    form.offerNumber,
    form.validUntil,
    form.vatRate,
    hasErrors,
    marginValue,
    freightValue,
    palletUnitValue,
    palletCount,
    parsedCosts.result,
    quoteLines,
    selectedQuote,
    tender.client_contact_email,
    tender.currency,
    tender.id,
    tender.items,
  ])

  const debouncedPayload = useDebouncedValue(payload, 350)
  const preview = useQuery({
    queryKey: ['tender-price-preview', tender.id, debouncedPayload],
    queryFn: () => {
      if (debouncedPayload === null) {
        throw new Error('preview payload is missing')
      }
      return priceTender(tender.id, debouncedPayload)
    },
    enabled: debouncedPayload !== null,
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 15_000,
  })

  const create = useMutation({
    mutationFn: (value: PricePayload) => runTenderWorkflow(tender.id, value),
    onSuccess: async (result: TenderWorkflowResponse) => {
      if (result.offer === null) {
        setWorkflowError(
          result.errors.length > 0
            ? result.errors.join('; ')
            : 'The workflow did not produce an offer.',
        )
        return
      }
      setWorkflowError(null)
      setCreated(result.offer)
      toast.success(`Offer ${result.offer.offer_number} created`, {
        description: 'The draft is ready for the approval console.',
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['offers'] }),
        queryClient.invalidateQueries({ queryKey: ['tender-offers', tender.id] }),
        queryClient.invalidateQueries({ queryKey: ['tender', tender.id] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
    },
  })

  const setField = (field: keyof OfferForm, value: string): void => {
    setForm((current) => ({ ...current, [field]: value }))
  }

  const markTouched = (field: string): void => {
    setTouched((current) => new Set(current).add(field))
  }

  const fieldError = (field: keyof FormErrors): string | undefined => {
    const message = errors[field]
    if (typeof message !== 'string') {
      return undefined
    }
    return attempted || touched.has(field) ? message : undefined
  }

  const costError = (itemId: string): string | undefined => {
    const message = errors.costs[itemId]
    return attempted || touched.has(itemId) ? message : undefined
  }

  const focusFirstInvalid = (): void => {
    const first = FIELD_ORDER.find((field) => errors[field] !== undefined)
    if (first !== undefined) {
      formRef.current?.querySelector<HTMLElement>(`#offer-${first}`)?.focus()
      return
    }
    const firstCost = tender.items.find((item) => errors.costs[item.id] !== undefined)
    if (firstCost !== undefined) {
      const target = quoteLines.has(firstCost.id)
        ? formRef.current?.querySelector<HTMLElement>('#offer-quote')
        : formRef.current?.querySelector<HTMLElement>(`#offer-cost-${firstCost.id}`)
      target?.focus()
    }
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    setWorkflowError(null)
    if (create.isPending) {
      return
    }
    if (hasErrors || payload === null) {
      focusFirstInvalid()
      return
    }
    create.mutate(payload)
  }

  const authoritative = payload !== null ? preview.data : undefined
  const vatBreakdown = authoritative !== undefined ? formatVatBreakdown(authoritative.offer) : []

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Create offer for {tender.tender_number}</DialogTitle>
          <DialogDescription>
            Enter one net supplier cost per position, then adjust margin, freight and pallet
            assumptions. The API previews the authoritative totals before the draft is saved for
            four-eyes approval.
          </DialogDescription>
        </DialogHeader>

        {created !== null ? (
          <OfferSuccess offer={created} onClose={onClose} />
        ) : (
          <form ref={formRef} noValidate className="space-y-5" onSubmit={handleSubmit}>
            {attempted && hasErrors ? (
              <p
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive"
              >
                Fix the highlighted fields before creating the offer.
              </p>
            ) : null}

            {workflowError !== null || create.isError ? (
              <p
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive"
              >
                {workflowError ?? apiErrorMessage(create.error)}
              </p>
            ) : null}

            <section className="space-y-3" aria-labelledby="offer-details-heading">
              <h3 id="offer-details-heading" className="text-sm font-semibold">
                Offer details
              </h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <FieldShell
                  id="offer-offerNumber"
                  label="Offer number"
                  hint="Unique reference used by the approval console and ERPNext."
                  required
                  error={fieldError('offerNumber')}
                >
                  {({ describedBy, invalid }) => (
                    <Input
                      id="offer-offerNumber"
                      name="offerNumber"
                      value={form.offerNumber}
                      onChange={(event) => setField('offerNumber', event.target.value)}
                      onBlur={() => markTouched('offerNumber')}
                      aria-invalid={invalid}
                      aria-describedby={describedBy}
                      autoComplete="off"
                      spellCheck={false}
                      disabled={create.isPending}
                    />
                  )}
                </FieldShell>

                <FieldShell
                  id="offer-customerName"
                  label="Customer name"
                  hint="Legal customer name shown on the offer."
                  required
                  error={fieldError('customerName')}
                >
                  {({ describedBy, invalid }) => (
                    <Input
                      id="offer-customerName"
                      name="customerName"
                      value={form.customerName}
                      onChange={(event) => setField('customerName', event.target.value)}
                      onBlur={() => markTouched('customerName')}
                      aria-invalid={invalid}
                      aria-describedby={describedBy}
                      autoComplete="organization"
                      disabled={create.isPending}
                    />
                  )}
                </FieldShell>

                <FieldShell
                  id="offer-validUntil"
                  label="Valid until"
                  hint="The offer expires after this date."
                  required
                  error={fieldError('validUntil')}
                >
                  {({ describedBy, invalid }) => (
                    <Input
                      id="offer-validUntil"
                      name="validUntil"
                      type="date"
                      value={form.validUntil}
                      onChange={(event) => setField('validUntil', event.target.value)}
                      onBlur={() => markTouched('validUntil')}
                      aria-invalid={invalid}
                      aria-describedby={describedBy}
                      disabled={create.isPending}
                    />
                  )}
                </FieldShell>

                <FieldShell
                  id="offer-vatRate"
                  label="VAT rate"
                  hint="Applied to every line and surcharge; the preview splits VAT by rate."
                >
                  {({ describedBy }) => (
                    <Select
                      value={form.vatRate}
                      onValueChange={(value) => setField('vatRate', value)}
                      disabled={create.isPending}
                    >
                      <SelectTrigger id="offer-vatRate" aria-describedby={describedBy}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {VAT_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </FieldShell>
              </div>
            </section>

            <section className="space-y-3" aria-labelledby="offer-sourcing-heading">
              <h3 id="offer-sourcing-heading" className="text-sm font-semibold">
                Sourcing
              </h3>
              <div className="grid gap-3 lg:grid-cols-2">
                <FieldShell
                  id="offer-quote"
                  label="Supplier quote"
                  hint="Use a persisted quote where it covers positions; manual costs fill the gaps."
                >
                  {({ describedBy }) => (
                    <Select
                      value={selectedQuoteId}
                      onValueChange={setSelectedQuoteId}
                      disabled={create.isPending}
                    >
                      <SelectTrigger id="offer-quote" aria-describedby={describedBy}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="manual">Manual entry</SelectItem>
                        {(quotes.data?.quotes ?? []).map((quote) => (
                          <SelectItem key={quote.id} value={quote.id}>
                            {quote.supplier_name} · {quote.quote_reference}
                            {quote.total_net !== null
                              ? ` · ${formatCurrency(quote.total_net, quote.currency)}`
                              : ''}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </FieldShell>
                <div className="space-y-1">
                  <p className="text-sm font-medium">Line coverage</p>
                  {quotes.isLoading ? (
                    <p role="status" className="text-xs text-muted-foreground">
                      Loading supplier quotes…
                    </p>
                  ) : selectedQuote === undefined ? (
                    <p className="text-xs text-muted-foreground">
                      Manual entry is used for every position.
                    </p>
                  ) : (
                    <div
                      role="status"
                      className="rounded-md border border-border bg-muted/30 p-2 text-xs"
                    >
                      <p>
                        {selectedQuote.supplier_name} covers {coveredCount} of{' '}
                        {tender.items.length} position(s).
                      </p>
                      {missingItems.length > 0 ? (
                        <p className="mt-1 flex items-start gap-1 text-warning">
                          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                          Enter manual costs for{' '}
                          {missingItems.map((item) => item.position_number).join(', ')}.
                        </p>
                      ) : null}
                    </div>
                  )}
                  {quotes.isError ? (
                    <p role="alert" className="text-xs text-destructive">
                      Supplier quotes could not be loaded; manual entry remains available.
                    </p>
                  ) : null}
                </div>
              </div>
            </section>

            <fieldset className="space-y-3">
              <legend className="text-sm font-semibold">Supplier costs</legend>
              <p className="text-xs text-muted-foreground">
                Enter one net supplier cost per tender position. These values form the manual
                supplier quote used by the deterministic pricing engine.
              </p>
              <div className="max-h-64 overflow-auto rounded-md border border-border">
                <table className="w-full min-w-[540px] border-collapse text-left text-sm">
                  <caption className="sr-only">
                    Tender positions with a supplier cost input
                  </caption>
                  <thead className="sticky top-0 bg-card">
                    <tr className="border-b border-border">
                      <th scope="col" className="px-3 py-2 text-xs font-medium uppercase">
                        Pos.
                      </th>
                      <th scope="col" className="px-3 py-2 text-xs font-medium uppercase">
                        Description
                      </th>
                      <th scope="col" className="px-3 py-2 text-xs font-medium uppercase">
                        Quantity
                      </th>
                      <th scope="col" className="px-3 py-2 text-xs font-medium uppercase">
                        Supplier cost net
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {tender.items.map((item) => {
                      const quoteLine = quoteLines.get(item.id)
                      const error = costError(item.id)
                      const inputId = `offer-cost-${item.id}`
                      return (
                        <tr
                          key={item.id}
                          className={
                            selectedQuote !== undefined && quoteLine === undefined
                              ? 'border-b border-border bg-warning/5 last:border-0'
                              : 'border-b border-border last:border-0'
                          }
                        >
                          <td className="px-3 py-2 align-top font-mono text-xs">
                            {item.position_number}
                          </td>
                          <td className="px-3 py-2 align-top">
                            <span className="line-clamp-2">{item.short_text}</span>
                          </td>
                          <td className="whitespace-nowrap px-3 py-2 align-top text-xs">
                            {formatQuantity(item.quantity)} {unitLabel(item.unit)}
                          </td>
                          <td className="px-3 py-2 align-top">
                            {quoteLine !== undefined ? (
                              <div>
                                <p className="text-sm font-medium">
                                  {formatCurrency(quoteLine.unit_price_net, tender.currency)}
                                </p>
                                <p className="text-[11px] text-muted-foreground">
                                  From {selectedQuote?.quote_reference ?? 'quote'}
                                </p>
                              </div>
                            ) : (
                              <>
                                <Label htmlFor={inputId} className="sr-only">
                                  Supplier cost net for position {item.position_number}
                                </Label>
                                <Input
                                  id={inputId}
                                  name={`cost-${item.id}`}
                                  className="w-32"
                                  inputMode="decimal"
                                  autoComplete="off"
                                  placeholder="0.0000"
                                  value={costs[item.id] ?? ''}
                                  onChange={(event) =>
                                    setCosts((current) => ({
                                      ...current,
                                      [item.id]: event.target.value,
                                    }))
                                  }
                                  onBlur={() => markTouched(item.id)}
                                  aria-invalid={error !== undefined ? true : undefined}
                                  aria-describedby={
                                    error !== undefined ? `${inputId}-error` : undefined
                                  }
                                  disabled={create.isPending}
                                />
                                {error !== undefined ? (
                                  <p
                                    id={`${inputId}-error`}
                                    role="alert"
                                    className="mt-1 text-xs text-destructive"
                                  >
                                    {error}
                                  </p>
                                ) : null}
                              </>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </fieldset>

            <fieldset className="space-y-3">
              <legend className="text-sm font-semibold">Margin and surcharges</legend>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <FieldShell
                  id="offer-margin"
                  label="Margin"
                  hint="Percentage on top of the supplier cost."
                  required
                  error={fieldError('margin')}
                >
                  {({ describedBy, invalid }) => (
                    <div className="flex items-center gap-2">
                      <Input
                        id="offer-margin"
                        name="margin"
                        inputMode="decimal"
                        value={form.marginPercent}
                        onChange={(event) => setField('marginPercent', event.target.value)}
                        onBlur={() => markTouched('margin')}
                        aria-invalid={invalid}
                        aria-describedby={describedBy}
                        autoComplete="off"
                        disabled={create.isPending}
                      />
                      <span className="text-sm text-muted-foreground" aria-hidden="true">
                        %
                      </span>
                    </div>
                  )}
                </FieldShell>

                <FieldShell
                  id="offer-freight"
                  label="Freight flat"
                  hint="Zero-margin net freight charge."
                  error={fieldError('freight')}
                >
                  {({ describedBy, invalid }) => (
                    <Input
                      id="offer-freight"
                      name="freight"
                      inputMode="decimal"
                      value={form.freight}
                      onChange={(event) => setField('freight', event.target.value)}
                      onBlur={() => markTouched('freight')}
                      aria-invalid={invalid}
                      aria-describedby={describedBy}
                      autoComplete="off"
                      disabled={create.isPending}
                    />
                  )}
                </FieldShell>

                <FieldShell
                  id="offer-palletUnit"
                  label="Pallet unit price"
                  hint="Zero-margin net price per pallet."
                  error={fieldError('palletUnit')}
                >
                  {({ describedBy, invalid }) => (
                    <Input
                      id="offer-palletUnit"
                      name="palletUnit"
                      inputMode="decimal"
                      value={form.palletUnit}
                      onChange={(event) => setField('palletUnit', event.target.value)}
                      onBlur={() => markTouched('palletUnit')}
                      aria-invalid={invalid}
                      aria-describedby={describedBy}
                      autoComplete="off"
                      disabled={create.isPending}
                    />
                  )}
                </FieldShell>

                <FieldShell
                  id="offer-palletCount"
                  label="Pallet count"
                  hint="Number of pallets charged."
                  error={fieldError('palletCount')}
                >
                  {({ describedBy, invalid }) => (
                    <Input
                      id="offer-palletCount"
                      name="palletCount"
                      inputMode="numeric"
                      value={form.palletCount}
                      onChange={(event) => setField('palletCount', event.target.value)}
                      onBlur={() => markTouched('palletCount')}
                      aria-invalid={invalid}
                      aria-describedby={describedBy}
                      autoComplete="off"
                      disabled={create.isPending}
                    />
                  )}
                </FieldShell>
              </div>
            </fieldset>

            <section className="space-y-3" aria-labelledby="offer-preview-heading">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 id="offer-preview-heading" className="text-sm font-semibold">
                    Server preview
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    Authoritative totals from the deterministic pricing engine.
                  </p>
                </div>
                {preview.isFetching && authoritative !== undefined ? (
                  <span role="status" className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    Recalculating…
                  </span>
                ) : null}
              </div>

              {payload === null ? (
                <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                  Enter a valid supplier cost, margin, freight and pallet assumption to see the
                  calculated totals.
                </p>
              ) : preview.isError ? (
                <p
                  role="alert"
                  className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
                >
                  {apiErrorMessage(preview.error)}
                </p>
              ) : authoritative === undefined ? (
                <p role="status" className="rounded-md border border-border bg-muted/40 p-3 text-sm">
                  Calculating totals…
                </p>
              ) : (
                <div
                  aria-live="polite"
                  aria-atomic="true"
                  className="space-y-3 rounded-md border border-border bg-muted/20 p-3"
                >
                  <dl className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                        Net
                      </dt>
                      <dd>{formatCurrency(authoritative.offer.total_net, tender.currency)}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                        VAT
                      </dt>
                      <dd>{formatCurrency(authoritative.offer.total_vat, tender.currency)}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                        Gross
                      </dt>
                      <dd className="font-semibold">
                        {formatCurrency(authoritative.offer.total_gross, tender.currency)}
                      </dd>
                    </div>
                  </dl>

                  <table className="w-full border-collapse text-left text-sm">
                    <caption className="sr-only">VAT breakdown by rate from the API preview</caption>
                    <thead>
                      <tr className="border-b border-border">
                        <th scope="col" className="py-1 text-xs font-medium uppercase">
                          Rate
                        </th>
                        <th scope="col" className="py-1 text-right text-xs font-medium uppercase">
                          Taxable net
                        </th>
                        <th scope="col" className="py-1 text-right text-xs font-medium uppercase">
                          VAT
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {vatBreakdown.map((split) => (
                        <tr key={split.rate}>
                          <td className="py-1">{formatPercent(split.rate)}</td>
                          <td className="py-1 text-right">
                            {formatCurrency(split.taxable_amount, tender.currency)}
                          </td>
                          <td className="py-1 text-right">
                            {formatCurrency(split.tax_amount, tender.currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {authoritative.warnings.length > 0 ? (
                    <ul className="list-inside list-disc text-xs text-warning">
                      {authoritative.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              )}
            </section>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose} disabled={create.isPending}>
                Cancel
              </Button>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? <Loader2 className="animate-spin" /> : <Calculator />}
                Create offer
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
