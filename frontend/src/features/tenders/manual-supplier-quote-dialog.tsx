import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Plus } from 'lucide-react'
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
import { Textarea } from '@/components/ui/textarea'
import { apiErrorMessage } from '@/lib/api-error'
import { unitLabel } from '@/lib/labels'
import { createSupplierQuote } from '@/lib/sourcing-api'
import { formatQuantity } from '@/lib/utils'
import type { ManualSupplierQuoteRequest, Tender } from '@/types/domain'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface QuoteForm {
  supplierName: string
  supplierEmail: string
  quoteReference: string
  validUntil: string
  deliveryDays: string
  incoterms: string
  paymentTerms: string
}

interface FieldErrors {
  supplierName?: string
  supplierEmail?: string
  quoteReference?: string
  validUntil?: string
  deliveryDays?: string
  costs: Record<string, string>
}

function isoDateAfterDays(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

function parseCost(raw: string): string | null {
  const normalized = raw.trim().replace(',', '.')
  if (!/^\d+(?:\.\d{1,4})?$/.test(normalized)) {
    return null
  }
  const amount = Number.parseFloat(normalized)
  return Number.isFinite(amount) && amount > 0 ? amount.toFixed(4) : null
}

function parseDays(raw: string): number | null {
  const normalized = raw.trim()
  if (normalized.length === 0) {
    return null
  }
  if (!/^\d+$/.test(normalized)) {
    return null
  }
  const days = Number.parseInt(normalized, 10)
  return days <= 365 ? days : null
}

function FieldShell({
  id,
  label,
  hint,
  required = false,
  error,
  children,
}: {
  id: string
  label: string
  hint: string
  required?: boolean
  error?: string | undefined
  children: (props: { describedBy: string; invalid: boolean | undefined }) => React.ReactNode
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

export function ManualSupplierQuoteDialog({
  tender,
  onClose,
}: {
  tender: Tender
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const formRef = React.useRef<HTMLFormElement>(null)
  const supplierRef = React.useRef<HTMLInputElement>(null)
  const [attempted, setAttempted] = React.useState(false)
  const [touched, setTouched] = React.useState<ReadonlySet<string>>(new Set())
  const [form, setForm] = React.useState<QuoteForm>(() => ({
    supplierName: '',
    supplierEmail: '',
    quoteReference: `SQ-${tender.tender_number}-${Date.now().toString(36).toUpperCase()}`,
    validUntil: isoDateAfterDays(30),
    deliveryDays: '',
    incoterms: '',
    paymentTerms: '',
  }))
  const [costs, setCosts] = React.useState<Record<string, string>>(() =>
    Object.fromEntries(tender.items.map((item) => [item.id, ''])),
  )

  const errors: FieldErrors = {
    costs: Object.fromEntries(
      tender.items
        .filter((item) => parseCost(costs[item.id] ?? '') === null)
        .map((item) => [item.id, 'Enter a positive unit price with up to four decimals.']),
    ),
    ...(form.supplierName.trim().length === 0
      ? { supplierName: 'Enter the supplier name.' }
      : {}),
    ...(form.supplierEmail.trim().length > 0 && !EMAIL_RE.test(form.supplierEmail.trim())
      ? { supplierEmail: 'Enter an e-mail address like name@example.com.' }
      : {}),
    ...(form.quoteReference.trim().length === 0
      ? { quoteReference: 'Enter the supplier quote reference.' }
      : {}),
    ...(!/^\d{4}-\d{2}-\d{2}$/.test(form.validUntil) ||
    Number.isNaN(Date.parse(`${form.validUntil}T00:00:00`))
      ? { validUntil: 'Choose a valid date.' }
      : {}),
    ...(form.deliveryDays.trim().length > 0 && parseDays(form.deliveryDays) === null
      ? { deliveryDays: 'Enter a whole number of days between 0 and 365.' }
      : {}),
  }

  const hasErrors =
    errors.supplierName !== undefined ||
    errors.supplierEmail !== undefined ||
    errors.quoteReference !== undefined ||
    errors.validUntil !== undefined ||
    errors.deliveryDays !== undefined ||
    Object.keys(errors.costs).length > 0

  const create = useMutation({
    mutationFn: (payload: ManualSupplierQuoteRequest) => createSupplierQuote(payload),
    onSuccess: async (data) => {
      toast.success(`Supplier quote ${data.quote.quote_reference} saved`, {
        description: `${data.quote.supplier_name} · ${data.quote.line_count} line(s)`,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['supplier-quotes', tender.id] }),
        queryClient.invalidateQueries({ queryKey: ['tender', tender.id] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
      onClose()
    },
  })

  const setField = (field: keyof QuoteForm, value: string): void => {
    setForm((current) => ({ ...current, [field]: value }))
  }

  const markTouched = (field: string): void => {
    setTouched((current) => new Set(current).add(field))
  }

  const visibleError = (field: keyof Omit<FieldErrors, 'costs'>): string | undefined => {
    const message = errors[field]
    return attempted || touched.has(field) ? message : undefined
  }

  const costError = (itemId: string): string | undefined => {
    const message = errors.costs[itemId]
    return attempted || touched.has(itemId) ? message : undefined
  }

  const focusFirstInvalid = (): void => {
    const fields: (keyof Omit<FieldErrors, 'costs'>)[] = [
      'supplierName',
      'supplierEmail',
      'quoteReference',
      'validUntil',
      'deliveryDays',
    ]
    const first = fields.find((field) => errors[field] !== undefined)
    if (first !== undefined) {
      formRef.current?.querySelector<HTMLElement>(`#quote-${first}`)?.focus()
      return
    }
    const firstCost = tender.items.find((item) => errors.costs[item.id] !== undefined)
    if (firstCost !== undefined) {
      formRef.current?.querySelector<HTMLElement>(`#quote-cost-${firstCost.id}`)?.focus()
    }
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (create.isPending) {
      return
    }
    if (hasErrors) {
      focusFirstInvalid()
      return
    }
    const lines = tender.items.map((item, index) => {
      const cost = parseCost(costs[item.id] ?? '')
      if (cost === null) {
        throw new Error('validated cost missing')
      }
      return {
        line_number: index + 1,
        description: item.short_text,
        tender_item_id: item.id,
        quantity: item.quantity,
        unit: item.unit,
        unit_price_net: cost,
      }
    })
    create.mutate({
      tender_id: tender.id,
      supplier_name: form.supplierName.trim(),
      ...(form.supplierEmail.trim().length > 0
        ? { supplier_contact_email: form.supplierEmail.trim() }
        : {}),
      quote_reference: form.quoteReference.trim(),
      valid_until: form.validUntil,
      ...(form.incoterms.trim().length > 0 ? { incoterms: form.incoterms.trim() } : {}),
      ...(form.paymentTerms.trim().length > 0
        ? { payment_terms: form.paymentTerms.trim() }
        : {}),
      delivery_days: parseDays(form.deliveryDays),
      lines,
    })
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        className="max-h-[92vh] overflow-y-auto sm:max-w-3xl"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          supplierRef.current?.focus()
        }}
      >
        <form ref={formRef} noValidate className="space-y-4" onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Add supplier quote</DialogTitle>
            <DialogDescription>
              Enter a manual quotation for {tender.tender_number}. It is stored beside incoming
              n8n quotes and can fulfil offer positions.
            </DialogDescription>
          </DialogHeader>

          {attempted && hasErrors ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive"
            >
              Fix the highlighted quote fields before saving.
            </p>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2">
            <FieldShell
              id="quote-supplierName"
              label="Supplier"
              hint="Legal supplier name used in the comparison."
              required
              error={visibleError('supplierName')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  id="quote-supplierName"
                  ref={supplierRef}
                  name="supplierName"
                  value={form.supplierName}
                  onChange={(event) => setField('supplierName', event.target.value)}
                  onBlur={() => markTouched('supplierName')}
                  aria-invalid={invalid}
                  aria-describedby={describedBy}
                  autoComplete="organization"
                  disabled={create.isPending}
                />
              )}
            </FieldShell>
            <FieldShell
              id="quote-supplierEmail"
              label="Supplier e-mail"
              hint="Optional contact for follow-up questions."
              error={visibleError('supplierEmail')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  id="quote-supplierEmail"
                  name="supplierEmail"
                  type="email"
                  value={form.supplierEmail}
                  onChange={(event) => setField('supplierEmail', event.target.value)}
                  onBlur={() => markTouched('supplierEmail')}
                  aria-invalid={invalid}
                  aria-describedby={describedBy}
                  autoComplete="email"
                  disabled={create.isPending}
                />
              )}
            </FieldShell>
            <FieldShell
              id="quote-quoteReference"
              label="Quote reference"
              hint="Supplier document number; unique per supplier."
              required
              error={visibleError('quoteReference')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  id="quote-quoteReference"
                  name="quoteReference"
                  value={form.quoteReference}
                  onChange={(event) => setField('quoteReference', event.target.value)}
                  onBlur={() => markTouched('quoteReference')}
                  aria-invalid={invalid}
                  aria-describedby={describedBy}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={create.isPending}
                />
              )}
            </FieldShell>
            <FieldShell
              id="quote-validUntil"
              label="Valid until"
              hint="Last day this supplier price can be used."
              required
              error={visibleError('validUntil')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  id="quote-validUntil"
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
              id="quote-deliveryDays"
              label="Delivery days"
              hint="Optional lead time in calendar days."
              error={visibleError('deliveryDays')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  id="quote-deliveryDays"
                  name="deliveryDays"
                  inputMode="numeric"
                  value={form.deliveryDays}
                  onChange={(event) => setField('deliveryDays', event.target.value)}
                  onBlur={() => markTouched('deliveryDays')}
                  aria-invalid={invalid}
                  aria-describedby={describedBy}
                  autoComplete="off"
                  disabled={create.isPending}
                />
              )}
            </FieldShell>
            <FieldShell
              id="quote-incoterms"
              label="Incoterms"
              hint="Optional delivery term, for example DAP."
            >
              {({ describedBy }) => (
                <Input
                  id="quote-incoterms"
                  name="incoterms"
                  value={form.incoterms}
                  onChange={(event) => setField('incoterms', event.target.value)}
                  aria-describedby={describedBy}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={create.isPending}
                />
              )}
            </FieldShell>
            <div className="sm:col-span-2">
              <FieldShell
                id="quote-paymentTerms"
                label="Payment terms"
                hint="Optional supplier payment conditions."
              >
                {({ describedBy }) => (
                  <Textarea
                    id="quote-paymentTerms"
                    name="paymentTerms"
                    rows={2}
                    value={form.paymentTerms}
                    onChange={(event) => setField('paymentTerms', event.target.value)}
                    aria-describedby={describedBy}
                    disabled={create.isPending}
                  />
                )}
              </FieldShell>
            </div>
          </div>

          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold">Position prices</legend>
            <p className="text-xs text-muted-foreground">
              Enter one supplier unit price per tender position. The comparison total is calculated
              from the quoted quantities.
            </p>
            <div className="max-h-64 overflow-auto rounded-md border border-border">
              <table className="w-full min-w-[560px] border-collapse text-left text-sm">
                <caption className="sr-only">Tender positions with supplier unit prices</caption>
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
                      Unit price net
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {tender.items.map((item) => {
                    const error = costError(item.id)
                    const inputId = `quote-cost-${item.id}`
                    return (
                      <tr key={item.id} className="border-b border-border last:border-0">
                        <td className="px-3 py-2 align-top font-mono text-xs">
                          {item.position_number}
                        </td>
                        <td className="px-3 py-2 align-top">{item.short_text}</td>
                        <td className="whitespace-nowrap px-3 py-2 align-top text-xs">
                          {formatQuantity(item.quantity)} {unitLabel(item.unit)}
                        </td>
                        <td className="px-3 py-2 align-top">
                          <Label htmlFor={inputId} className="sr-only">
                            Supplier unit price for position {item.position_number}
                          </Label>
                          <Input
                            id={inputId}
                            name={`cost-${item.id}`}
                            inputMode="decimal"
                            className="w-32"
                            placeholder="0.0000"
                            autoComplete="off"
                            value={costs[item.id] ?? ''}
                            onChange={(event) =>
                              setCosts((current) => ({
                                ...current,
                                [item.id]: event.target.value,
                              }))
                            }
                            onBlur={() => markTouched(item.id)}
                            aria-invalid={error !== undefined ? true : undefined}
                            aria-describedby={error !== undefined ? `${inputId}-error` : undefined}
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
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </fieldset>

          {create.isError ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
            >
              {apiErrorMessage(create.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
              Save supplier quote
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
