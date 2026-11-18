import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Plus, Trash2 } from 'lucide-react'
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
  createSupplier,
  updateSupplier,
  type SupplierContactPayload,
  type SupplierPayload,
} from '@/lib/master-data-api'
import type { Supplier } from '@/types/domain'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const COUNTRY_RE = /^[A-Za-z]{2}$/

interface ContactDraft {
  key: string
  id?: string
  name: string
  role: string
  email: string
  phone: string
  is_primary: boolean
}

interface FormValues {
  name: string
  legalName: string
  email: string
  phone: string
  street: string
  postalCode: string
  city: string
  countryCode: string
  paymentTerms: string
  deliveryDays: string
  notes: string
  isActive: boolean
}

function key(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `contact-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function emptyContact(primary: boolean): ContactDraft {
  return { key: key(), name: '', role: '', email: '', phone: '', is_primary: primary }
}

function initialValues(supplier: Supplier | null): FormValues {
  return {
    name: supplier?.name ?? '',
    legalName: supplier?.legal_name ?? '',
    email: supplier?.email ?? '',
    phone: supplier?.phone ?? '',
    street: supplier?.street ?? '',
    postalCode: supplier?.postal_code ?? '',
    city: supplier?.city ?? '',
    countryCode: supplier?.country_code ?? 'DE',
    paymentTerms: supplier?.payment_terms ?? '',
    deliveryDays: supplier?.delivery_days?.toString() ?? '',
    notes: supplier?.notes ?? '',
    isActive: supplier?.is_active ?? true,
  }
}

function initialContacts(supplier: Supplier | null): ContactDraft[] {
  if (supplier === null || supplier.contacts.length === 0) {
    return [emptyContact(true)]
  }
  return supplier.contacts.map((contact) => ({
    key: contact.id,
    id: contact.id,
    name: contact.name,
    role: contact.role ?? '',
    email: contact.email ?? '',
    phone: contact.phone ?? '',
    is_primary: contact.is_primary,
  }))
}

export function SupplierDialog({
  supplier,
  onClose,
}: {
  supplier: Supplier | null
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const formRef = React.useRef<HTMLFormElement>(null)
  const nameRef = React.useRef<HTMLInputElement>(null)
  const [values, setValues] = React.useState<FormValues>(() => initialValues(supplier))
  const [contacts, setContacts] = React.useState<ContactDraft[]>(() => initialContacts(supplier))
  const [attempted, setAttempted] = React.useState(false)

  const errors: Record<string, string> = {}
  if (!values.name.trim()) errors.name = 'Enter the supplier name.'
  if (values.email.trim() && !EMAIL_RE.test(values.email.trim())) {
    errors.email = 'Enter a valid e-mail address.'
  }
  if (!COUNTRY_RE.test(values.countryCode.trim())) {
    errors.countryCode = 'Use a two-letter country code such as DE.'
  }
  const deliveryDays = values.deliveryDays.trim()
  if (
    deliveryDays.length > 0 &&
    (!/^\d+$/.test(deliveryDays) ||
      Number.parseInt(deliveryDays, 10) < 0 ||
      Number.parseInt(deliveryDays, 10) > 365)
  ) {
    errors.deliveryDays = 'Delivery days must be between 0 and 365.'
  }
  contacts.forEach((contact, index) => {
    if (!contact.name.trim()) errors[`contact-name-${index}`] = 'Enter the contact name.'
    if (contact.email.trim() && !EMAIL_RE.test(contact.email.trim())) {
      errors[`contact-email-${index}`] = 'Enter a valid e-mail address.'
    }
  })

  const mutation = useMutation({
    mutationFn: (payload: SupplierPayload) =>
      supplier === null ? createSupplier(payload) : updateSupplier(supplier.id, payload),
    onSuccess: async (saved) => {
      toast.success(supplier === null ? 'Supplier created' : 'Supplier updated', {
        description: saved.name,
      })
      onClose()
      await queryClient.invalidateQueries({ queryKey: ['master-data', 'suppliers'] })
      await queryClient.invalidateQueries({ queryKey: ['master-data', 'customers'] })
    },
  })

  const update = (field: keyof FormValues, value: string | boolean): void => {
    setValues((current) => ({ ...current, [field]: value }))
  }

  const updateContact = (contactKey: string, patch: Partial<ContactDraft>): void => {
    setContacts((current) =>
      current.map((contact) => (contact.key === contactKey ? { ...contact, ...patch } : contact)),
    )
  }

  const makePrimary = (contactKey: string): void => {
    setContacts((current) =>
      current.map((contact) => ({ ...contact, is_primary: contact.key === contactKey })),
    )
  }

  const addContact = (): void => {
    setContacts((current) => [...current, emptyContact(current.length === 0)])
  }

  const removeContact = (contactKey: string): void => {
    setContacts((current) => {
      const remaining = current.filter((contact) => contact.key !== contactKey)
      if (remaining.length > 0 && !remaining.some((contact) => contact.is_primary)) {
        const first = remaining[0]
        if (first !== undefined) {
          remaining[0] = { ...first, is_primary: true }
        }
      }
      return remaining
    })
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (mutation.isPending) return
    setAttempted(true)
    if (Object.keys(errors).length > 0) {
      const first = Object.keys(errors)[0] ?? 'supplier-name'
      const target = formRef.current?.querySelector<HTMLElement>(`#${CSS.escape(first)}`)
      ;(target ?? nameRef.current)?.focus()
      return
    }
    const contactPayload: SupplierContactPayload[] = contacts
      .filter((contact) => contact.name.trim().length > 0)
      .map((contact) => ({
        ...(contact.id !== undefined ? { id: contact.id } : {}),
        name: contact.name.trim(),
        role: contact.role.trim() || null,
        email: contact.email.trim() || null,
        phone: contact.phone.trim() || null,
        is_primary: contact.is_primary,
      }))
    mutation.mutate({
      name: values.name.trim(),
      legal_name: values.legalName.trim() || null,
      email: values.email.trim() || null,
      phone: values.phone.trim() || null,
      street: values.street.trim() || null,
      postal_code: values.postalCode.trim() || null,
      city: values.city.trim() || null,
      country_code: values.countryCode.trim().toUpperCase(),
      payment_terms: values.paymentTerms.trim() || null,
      delivery_days: deliveryDays.length > 0 ? Number.parseInt(deliveryDays, 10) : null,
      notes: values.notes.trim() || null,
      is_active: values.isActive,
      contacts: contactPayload,
    })
  }

  const error = (id: string): string | undefined => (attempted ? errors[id] : undefined)

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-4xl">
        <form ref={formRef} noValidate className="space-y-4" onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{supplier === null ? 'Create supplier' : 'Edit supplier'}</DialogTitle>
            <DialogDescription>
              Supplier master data feeds RFQ address-book selection and procurement workflows.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="supplier-name">Name *</Label>
              <Input
                id="supplier-name"
                ref={nameRef}
                value={values.name}
                onChange={(event) => update('name', event.target.value)}
                aria-invalid={error('name') ? true : undefined}
                aria-describedby={error('name') ? 'supplier-name-error' : undefined}
                autoComplete="organization"
              />
              {error('name') ? (
                <p id="supplier-name-error" role="alert" className="text-xs text-destructive">
                  {error('name')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-legal-name">Legal name</Label>
              <Input
                id="supplier-legal-name"
                value={values.legalName}
                onChange={(event) => update('legalName', event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-email">E-mail</Label>
              <Input
                id="supplier-email"
                type="email"
                value={values.email}
                onChange={(event) => update('email', event.target.value)}
                aria-invalid={error('email') ? true : undefined}
                aria-describedby={error('email') ? 'supplier-email-error' : undefined}
                autoComplete="email"
              />
              {error('email') ? (
                <p id="supplier-email-error" role="alert" className="text-xs text-destructive">
                  {error('email')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-phone">Phone</Label>
              <Input
                id="supplier-phone"
                value={values.phone}
                onChange={(event) => update('phone', event.target.value)}
                autoComplete="tel"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-street">Street</Label>
              <Input
                id="supplier-street"
                value={values.street}
                onChange={(event) => update('street', event.target.value)}
                autoComplete="street-address"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-postal-code">Postal code</Label>
              <Input
                id="supplier-postal-code"
                value={values.postalCode}
                onChange={(event) => update('postalCode', event.target.value)}
                autoComplete="postal-code"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-city">City</Label>
              <Input
                id="supplier-city"
                value={values.city}
                onChange={(event) => update('city', event.target.value)}
                autoComplete="address-level2"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-country">Country code</Label>
              <Input
                id="supplier-country"
                value={values.countryCode}
                maxLength={2}
                onChange={(event) => update('countryCode', event.target.value)}
                aria-invalid={error('countryCode') ? true : undefined}
              />
              {error('countryCode') ? (
                <p role="alert" className="text-xs text-destructive">
                  {error('countryCode')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-delivery-days">Delivery days</Label>
              <Input
                id="supplier-delivery-days"
                inputMode="numeric"
                value={values.deliveryDays}
                onChange={(event) => update('deliveryDays', event.target.value)}
                aria-invalid={error('deliveryDays') ? true : undefined}
              />
              {error('deliveryDays') ? (
                <p role="alert" className="text-xs text-destructive">
                  {error('deliveryDays')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="supplier-payment-terms">Payment terms</Label>
              <Input
                id="supplier-payment-terms"
                value={values.paymentTerms}
                onChange={(event) => update('paymentTerms', event.target.value)}
                maxLength={512}
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="supplier-notes">Notes</Label>
              <Textarea
                id="supplier-notes"
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
              Active supplier
            </label>
          </div>

          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold">Contacts</legend>
            <p className="text-xs text-muted-foreground">
              One primary contact is used to prefill RFQ dispatch.
            </p>
            {contacts.map((contact, index) => (
              <div key={contact.key} className="rounded-md border border-border p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Contact {index + 1}
                  </p>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove contact ${index + 1}`}
                    disabled={contacts.length === 1}
                    onClick={() => removeContact(contact.key)}
                  >
                    <Trash2 />
                    Remove
                  </Button>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor={`contact-name-${index}`}>Name *</Label>
                    <Input
                      id={`contact-name-${index}`}
                      value={contact.name}
                      onChange={(event) =>
                        updateContact(contact.key, { name: event.target.value })
                      }
                      aria-invalid={error(`contact-name-${index}`) ? true : undefined}
                      aria-describedby={
                        error(`contact-name-${index}`)
                          ? `contact-name-${index}-error`
                          : undefined
                      }
                    />
                    {error(`contact-name-${index}`) ? (
                      <p
                        id={`contact-name-${index}-error`}
                        role="alert"
                        className="text-xs text-destructive"
                      >
                        {error(`contact-name-${index}`)}
                      </p>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`contact-role-${index}`}>Role</Label>
                    <Input
                      id={`contact-role-${index}`}
                      value={contact.role}
                      onChange={(event) =>
                        updateContact(contact.key, { role: event.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`contact-email-${index}`}>E-mail</Label>
                    <Input
                      id={`contact-email-${index}`}
                      type="email"
                      value={contact.email}
                      onChange={(event) =>
                        updateContact(contact.key, { email: event.target.value })
                      }
                      aria-invalid={error(`contact-email-${index}`) ? true : undefined}
                      aria-describedby={
                        error(`contact-email-${index}`)
                          ? `contact-email-${index}-error`
                          : undefined
                      }
                    />
                    {error(`contact-email-${index}`) ? (
                      <p
                        id={`contact-email-${index}-error`}
                        role="alert"
                        className="text-xs text-destructive"
                      >
                        {error(`contact-email-${index}`)}
                      </p>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`contact-phone-${index}`}>Phone</Label>
                    <Input
                      id={`contact-phone-${index}`}
                      value={contact.phone}
                      onChange={(event) =>
                        updateContact(contact.key, { phone: event.target.value })
                      }
                    />
                  </div>
                </div>
                <label className="mt-2 flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="primary-supplier-contact"
                    checked={contact.is_primary}
                    onChange={() => makePrimary(contact.key)}
                  />
                  Primary contact
                </label>
              </div>
            ))}
            <Button type="button" variant="outline" size="sm" onClick={addContact}>
              <Plus />
              Add contact
            </Button>
          </fieldset>

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
              Save supplier
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
