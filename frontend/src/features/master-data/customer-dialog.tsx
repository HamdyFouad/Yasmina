import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
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
import {
  createCustomer,
  updateCustomer,
  type CustomerPayload,
} from '@/lib/master-data-api'
import type { Customer } from '@/types/domain'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const COUNTRY_RE = /^[A-Za-z]{2}$/

interface FormValues {
  name: string
  legalName: string
  street: string
  postalCode: string
  city: string
  countryCode: string
  vatId: string
  leitwegId: string
  paymentTerms: string
  contactName: string
  contactEmail: string
  phone: string
  notes: string
  isActive: boolean
}

function initialValues(customer: Customer | null): FormValues {
  return {
    name: customer?.name ?? '',
    legalName: customer?.legal_name ?? '',
    street: customer?.street ?? '',
    postalCode: customer?.postal_code ?? '',
    city: customer?.city ?? '',
    countryCode: customer?.country_code ?? 'DE',
    vatId: customer?.vat_id ?? '',
    leitwegId: customer?.leitweg_id ?? '',
    paymentTerms: customer?.payment_terms ?? '',
    contactName: customer?.contact_name ?? '',
    contactEmail: customer?.contact_email ?? '',
    phone: customer?.phone ?? '',
    notes: customer?.notes ?? '',
    isActive: customer?.is_active ?? true,
  }
}

export function CustomerDialog({
  customer,
  onClose,
}: {
  customer: Customer | null
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const formRef = React.useRef<HTMLFormElement>(null)
  const nameRef = React.useRef<HTMLInputElement>(null)
  const [values, setValues] = React.useState<FormValues>(() => initialValues(customer))
  const [attempted, setAttempted] = React.useState(false)

  const errors: Record<string, string> = {}
  if (!values.name.trim()) errors.name = 'Enter the customer name.'
  if (!values.street.trim()) errors.street = 'Enter the billing street.'
  if (!values.postalCode.trim()) errors.postalCode = 'Enter the billing postal code.'
  if (!values.city.trim()) errors.city = 'Enter the billing city.'
  if (!COUNTRY_RE.test(values.countryCode.trim())) {
    errors.countryCode = 'Use a two-letter country code such as DE.'
  }
  if (values.vatId.trim().length > 32) {
    errors.vatId = 'The VAT ID must be 32 characters or fewer.'
  }
  const leitweg = values.leitwegId.trim()
  if (leitweg.length > 0 && (leitweg.length < 5 || leitweg.length > 46)) {
    errors.leitwegId = 'The Leitweg-ID must be 5 to 46 characters.'
  }
  if (values.contactEmail.trim() && !EMAIL_RE.test(values.contactEmail.trim())) {
    errors.contactEmail = 'Enter a valid e-mail address.'
  }

  const mutation = useMutation({
    mutationFn: (payload: CustomerPayload) =>
      customer === null ? createCustomer(payload) : updateCustomer(customer.id, payload),
    onSuccess: async (saved) => {
      toast.success(customer === null ? 'Customer created' : 'Customer updated', {
        description: saved.name,
      })
      onClose()
      await queryClient.invalidateQueries({ queryKey: ['master-data', 'customers'] })
    },
  })

  const update = (field: keyof FormValues, value: string | boolean): void => {
    setValues((current) => ({ ...current, [field]: value }))
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (mutation.isPending) return
    setAttempted(true)
    if (Object.keys(errors).length > 0) {
      const first = Object.keys(errors)[0]
      const target = formRef.current?.querySelector<HTMLElement>(`#customer-${first}`)
      ;(target ?? nameRef.current)?.focus()
      return
    }
    mutation.mutate({
      name: values.name.trim(),
      legal_name: values.legalName.trim() || null,
      street: values.street.trim(),
      postal_code: values.postalCode.trim(),
      city: values.city.trim(),
      country_code: values.countryCode.trim().toUpperCase(),
      vat_id: values.vatId.trim() || null,
      leitweg_id: leitweg || null,
      payment_terms: values.paymentTerms.trim() || null,
      contact_name: values.contactName.trim() || null,
      contact_email: values.contactEmail.trim() || null,
      phone: values.phone.trim() || null,
      notes: values.notes.trim() || null,
      is_active: values.isActive,
    })
  }

  const fieldError = (field: string): string | undefined => (attempted ? errors[field] : undefined)

  const errorText = (field: string): React.ReactNode =>
    fieldError(field) ? (
      <p role="alert" className="text-xs text-destructive">
        {fieldError(field)}
      </p>
    ) : null

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <form ref={formRef} noValidate className="space-y-4" onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{customer === null ? 'Create customer' : 'Edit customer'}</DialogTitle>
            <DialogDescription>
              Saved customers prefill billing address, VAT ID, Leitweg-ID and payment terms in the
              e-invoice dialog.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="customer-name">Name *</Label>
              <Input
                id="customer-name"
                ref={nameRef}
                value={values.name}
                onChange={(event) => update('name', event.target.value)}
                aria-invalid={fieldError('name') ? true : undefined}
                autoComplete="organization"
              />
              {errorText('name')}
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="customer-legalName">Legal name</Label>
              <Input
                id="customer-legalName"
                value={values.legalName}
                onChange={(event) => update('legalName', event.target.value)}
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="customer-street">Billing street *</Label>
              <Input
                id="customer-street"
                value={values.street}
                onChange={(event) => update('street', event.target.value)}
                aria-invalid={fieldError('street') ? true : undefined}
                autoComplete="street-address"
              />
              {errorText('street')}
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-postalCode">Postal code *</Label>
              <Input
                id="customer-postalCode"
                value={values.postalCode}
                onChange={(event) => update('postalCode', event.target.value)}
                aria-invalid={fieldError('postalCode') ? true : undefined}
                autoComplete="postal-code"
              />
              {errorText('postalCode')}
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-city">City *</Label>
              <Input
                id="customer-city"
                value={values.city}
                onChange={(event) => update('city', event.target.value)}
                aria-invalid={fieldError('city') ? true : undefined}
                autoComplete="address-level2"
              />
              {errorText('city')}
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-countryCode">Country code</Label>
              <Input
                id="customer-countryCode"
                value={values.countryCode}
                maxLength={2}
                onChange={(event) => update('countryCode', event.target.value)}
                aria-invalid={fieldError('countryCode') ? true : undefined}
              />
              {errorText('countryCode')}
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-vatId">VAT ID</Label>
              <Input
                id="customer-vatId"
                value={values.vatId}
                maxLength={32}
                onChange={(event) => update('vatId', event.target.value)}
                aria-invalid={fieldError('vatId') ? true : undefined}
              />
              {errorText('vatId')}
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-leitwegId">Leitweg-ID</Label>
              <Input
                id="customer-leitwegId"
                value={values.leitwegId}
                maxLength={46}
                onChange={(event) => update('leitwegId', event.target.value)}
                aria-invalid={fieldError('leitwegId') ? true : undefined}
              />
              {errorText('leitwegId')}
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-paymentTerms">Payment terms</Label>
              <Input
                id="customer-paymentTerms"
                value={values.paymentTerms}
                maxLength={512}
                onChange={(event) => update('paymentTerms', event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-contactName">Contact name</Label>
              <Input
                id="customer-contactName"
                value={values.contactName}
                onChange={(event) => update('contactName', event.target.value)}
                autoComplete="name"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-contactEmail">Contact e-mail</Label>
              <Input
                id="customer-contactEmail"
                type="email"
                value={values.contactEmail}
                onChange={(event) => update('contactEmail', event.target.value)}
                aria-invalid={fieldError('contactEmail') ? true : undefined}
                autoComplete="email"
              />
              {errorText('contactEmail')}
            </div>
            <div className="space-y-1">
              <Label htmlFor="customer-phone">Phone</Label>
              <Input
                id="customer-phone"
                value={values.phone}
                onChange={(event) => update('phone', event.target.value)}
                autoComplete="tel"
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="customer-notes">Notes</Label>
              <Textarea
                id="customer-notes"
                rows={2}
                value={values.notes}
                onChange={(event) => update('notes', event.target.value)}
              />
            </div>
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <input
                type="checkbox"
                checked={values.isActive}
                onChange={(event) => update('isActive', event.target.checked)}
              />
              Active customer
            </label>
          </div>

          {mutation.isError ? (
            <p role="alert" className="text-xs text-destructive">
              {apiErrorMessage(mutation.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? <Loader2 className="animate-spin" /> : null}
              Save customer
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
