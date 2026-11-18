import * as React from 'react'

import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import type { Customer, CustomerOffer, InvoiceRequestPayload } from '@/types/domain'

export interface InvoiceFormValues {
  street: string
  city: string
  postalCode: string
  countryCode: string
  vatId: string
  email: string
  buyerReference: string
  dueDate: string
  iban: string
  paymentTerms: string
}

export type InvoiceField = keyof InvoiceFormValues
export type InvoiceErrors = Partial<Record<InvoiceField, string>>

export const INVOICE_FIELD_ORDER: InvoiceField[] = [
  'street',
  'city',
  'postalCode',
  'countryCode',
  'vatId',
  'email',
  'buyerReference',
  'dueDate',
  'iban',
  'paymentTerms',
]

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const COUNTRY_RE = /^[A-Za-z]{2}$/

function isoDateAfterDays(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

export function createInvoiceForm(offer: CustomerOffer): InvoiceFormValues {
  return {
    street: '',
    city: '',
    postalCode: '',
    countryCode: 'DE',
    vatId: '',
    email: offer.customer_contact_email ?? '',
    buyerReference: '',
    dueDate: isoDateAfterDays(14),
    iban: '',
    paymentTerms: '',
  }
}

export function applyCustomerToInvoiceForm(
  values: InvoiceFormValues,
  customer: Customer,
): InvoiceFormValues {
  return {
    ...values,
    street: customer.street,
    city: customer.city,
    postalCode: customer.postal_code,
    countryCode: customer.country_code,
    vatId: customer.vat_id ?? '',
    email: customer.contact_email ?? values.email,
    buyerReference: customer.leitweg_id ?? values.buyerReference,
    paymentTerms: customer.payment_terms ?? values.paymentTerms,
  }
}

export function validateInvoiceForm(values: InvoiceFormValues): InvoiceErrors {
  const errors: InvoiceErrors = {}
  if (!values.street.trim()) {
    errors.street = "Enter the buyer's street and house number."
  }
  if (!values.city.trim()) {
    errors.city = "Enter the buyer's city."
  }
  if (!values.postalCode.trim()) {
    errors.postalCode = "Enter the buyer's postal code."
  }
  if (!COUNTRY_RE.test(values.countryCode.trim())) {
    errors.countryCode = 'Use a two-letter country code such as DE.'
  }
  if (values.vatId.trim().length > 32) {
    errors.vatId = 'The VAT ID must be 32 characters or fewer.'
  }
  if (values.email.trim() && !EMAIL_RE.test(values.email.trim())) {
    errors.email = 'Enter an e-mail address like name@example.com.'
  }
  const reference = values.buyerReference.trim()
  if (!reference) {
    errors.buyerReference = 'Enter the Leitweg-ID or buyer reference.'
  } else if (reference.length < 5 || reference.length > 46) {
    errors.buyerReference = 'The buyer reference must be 5 to 46 characters long.'
  }
  if (!values.dueDate) {
    errors.dueDate = 'Choose a due date.'
  } else if (Number.isNaN(Date.parse(`${values.dueDate}T00:00:00`))) {
    errors.dueDate = 'Choose a valid due date.'
  }
  if (values.iban.replace(/\s+/g, '').length > 34) {
    errors.iban = 'The IBAN must be 34 characters or fewer.'
  }
  if (values.paymentTerms.length > 512) {
    errors.paymentTerms = 'Payment terms must be 512 characters or fewer.'
  }
  return errors
}

export function toInvoicePayload(values: InvoiceFormValues): InvoiceRequestPayload {
  const iban = values.iban.replace(/\s+/g, '').toUpperCase()
  const vatId = values.vatId.trim()
  const email = values.email.trim()
  const paymentTerms = values.paymentTerms.trim()
  return {
    buyer: {
      street: values.street.trim(),
      city: values.city.trim(),
      postal_code: values.postalCode.trim(),
      country_code: values.countryCode.trim().toUpperCase(),
      ...(vatId ? { vat_id: vatId } : {}),
      ...(email ? { email } : {}),
    },
    buyer_reference: values.buyerReference.trim(),
    due_date: values.dueDate,
    ...(iban ? { iban } : {}),
    ...(paymentTerms ? { payment_terms: paymentTerms } : {}),
  }
}

function FieldShell({
  id,
  label,
  hint,
  required,
  error,
  children,
}: {
  id: string
  label: string
  hint: string
  required?: boolean
  error: string | undefined
  children: (props: { describedBy: string; invalid: boolean | undefined }) => React.ReactNode
}) {
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = error !== undefined ? `${hintId} ${errorId}` : hintId
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </Label>
      <p id={hintId} className="text-xs text-muted-foreground">
        {hint}
      </p>
      {children({ describedBy, invalid: error !== undefined ? true : undefined })}
      {error !== undefined ? (
        <p id={errorId} className="text-xs text-destructive">
          <span aria-hidden="true">! </span>
          {error}
        </p>
      ) : null}
    </div>
  )
}

export function InvoiceFields({
  enabled,
  onToggle,
  values,
  errors,
  touched,
  attempted,
  onChange,
  onBlur,
  customers,
  selectedCustomerId,
  onSelectCustomer,
}: {
  enabled: boolean
  onToggle: (enabled: boolean) => void
  values: InvoiceFormValues
  errors: InvoiceErrors
  touched: ReadonlySet<InvoiceField>
  attempted: boolean
  onChange: (field: InvoiceField, value: string) => void
  onBlur: (field: InvoiceField) => void
  customers: Customer[]
  selectedCustomerId: string
  onSelectCustomer: (customerId: string) => void
}) {
  const fieldError = (field: InvoiceField): string | undefined =>
    attempted || touched.has(field) ? errors[field] : undefined

  const bind = (field: InvoiceField) => ({
    id: `invoice-${field}`,
    name: field,
    value: values[field],
    onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
      onChange(field, event.target.value),
    onBlur: () => onBlur(field),
  })

  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        aria-expanded={enabled}
        aria-controls="approval-invoice-section"
        className="flex w-full items-center justify-between gap-2 rounded-md p-3 text-left text-sm font-medium hover:bg-accent/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        onClick={() => onToggle(!enabled)}
      >
        <span>Generate e-invoice</span>
        <span aria-hidden="true" className="text-xs font-normal text-muted-foreground">
          {enabled ? 'Hide' : 'Optional'}
        </span>
      </button>

      <div
        id="approval-invoice-section"
        hidden={!enabled}
        className="space-y-4 border-t border-border p-3"
      >
        <p className="text-xs text-muted-foreground">
          Adds a ZUGFeRD PDF/A-3 and an XRechnung XML to the approval. Leave collapsed to approve
          without invoicing.
        </p>

        <div className="space-y-1">
          <Label htmlFor="invoice-customer">Saved customer</Label>
          <p id="invoice-customer-hint" className="text-xs text-muted-foreground">
            Selecting a customer prefills the billing address, VAT ID, Leitweg-ID and terms.
          </p>
          <Select
            value={selectedCustomerId || 'none'}
            onValueChange={(value) => onSelectCustomer(value === 'none' ? '' : value)}
          >
            <SelectTrigger id="invoice-customer" aria-describedby="invoice-customer-hint">
              <SelectValue placeholder="Manual buyer details" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">Manual buyer details</SelectItem>
              {customers.map((customer) => (
                <SelectItem key={customer.id} value={customer.id}>
                  {customer.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {attempted && Object.keys(errors).length > 0 ? (
          <p
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive"
          >
            Fix the highlighted invoice fields before approving the offer.
          </p>
        ) : null}

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">Buyer</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <FieldShell
                id="invoice-street"
                label="Street and house number"
                hint="Street of the invoice recipient, including the house number."
                required
                error={fieldError('street')}
              >
                {({ describedBy, invalid }) => (
                  <Input
                    {...bind('street')}
                    autoComplete="street-address"
                    maxLength={256}
                    required
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                  />
                )}
              </FieldShell>
            </div>
            <FieldShell
              id="invoice-postalCode"
              label="Postal code"
              hint="Postal code of the invoice recipient."
              required
              error={fieldError('postalCode')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('postalCode')}
                  autoComplete="postal-code"
                  maxLength={32}
                  required
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
            <FieldShell
              id="invoice-city"
              label="City"
              hint="City of the invoice recipient."
              required
              error={fieldError('city')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('city')}
                  autoComplete="address-level2"
                  maxLength={128}
                  required
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
            <FieldShell
              id="invoice-countryCode"
              label="Country code"
              hint="Two-letter ISO code such as DE."
              required
              error={fieldError('countryCode')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('countryCode')}
                  autoComplete="country"
                  maxLength={2}
                  required
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
            <FieldShell
              id="invoice-vatId"
              label="VAT ID"
              hint="Optional. Printed on the invoice when provided."
              error={fieldError('vatId')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('vatId')}
                  maxLength={32}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
            <FieldShell
              id="invoice-email"
              label="Buyer e-mail"
              hint="Optional. Used as the buyer's e-invoice endpoint."
              error={fieldError('email')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('email')}
                  type="email"
                  autoComplete="email"
                  maxLength={254}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
          </div>
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">Invoice terms</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <FieldShell
              id="invoice-buyerReference"
              label="Buyer reference (Leitweg-ID)"
              hint="Leitweg-ID or buyer reference, 5 to 46 characters."
              required
              error={fieldError('buyerReference')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('buyerReference')}
                  maxLength={46}
                  required
                  autoComplete="off"
                  spellCheck={false}
                  autoCapitalize="none"
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
            <FieldShell
              id="invoice-dueDate"
              label="Due date"
              hint="Payment due date printed on the invoice."
              required
              error={fieldError('dueDate')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('dueDate')}
                  type="date"
                  required
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
            <FieldShell
              id="invoice-iban"
              label="IBAN"
              hint="Optional. Defaults to the configured company IBAN."
              error={fieldError('iban')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('iban')}
                  maxLength={34}
                  autoComplete="off"
                  spellCheck={false}
                  autoCapitalize="characters"
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
            <FieldShell
              id="invoice-paymentTerms"
              label="Payment terms"
              hint="Optional. For example: payable within 14 days."
              error={fieldError('paymentTerms')}
            >
              {({ describedBy, invalid }) => (
                <Input
                  {...bind('paymentTerms')}
                  maxLength={512}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </FieldShell>
          </div>
        </fieldset>
      </div>
    </div>
  )
}
