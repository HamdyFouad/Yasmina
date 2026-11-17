import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Plus, RadioTower, Trash2 } from 'lucide-react'
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
import { apiErrorMessage, isApiError } from '@/lib/api-error'
import { dispatchRfq } from '@/lib/sourcing-api'
import type { RfqDispatchRequest, RfqDispatchResponse, RfqSupplier, Tender } from '@/types/domain'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface SupplierDraft extends RfqSupplier {
  key: string
}

function randomId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `rfq-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function stableHash(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

function newSupplier(): SupplierDraft {
  return {
    key: randomId(),
    name: '',
    email: '',
    contact_name: '',
  }
}

function errorCopy(error: unknown): { title: string; detail: string } {
  if (isApiError(error)) {
    if (error.status === 409) {
      return {
        title: 'An identical RFQ is already pending',
        detail:
          'The reservation is still inside the dispatch TTL. Retry with the same dispatch after it expires, or wait for n8n to finish the current request.',
      }
    }
    if (error.status === 503) {
      return {
        title: 'RFQ dispatch is not configured',
        detail: error.detail,
      }
    }
    if (error.status === 502) {
      return {
        title: 'n8n rejected the RFQ',
        detail: error.detail,
      }
    }
  }
  return { title: 'RFQ dispatch failed', detail: apiErrorMessage(error) }
}

function DispatchResult({ result }: { result: RfqDispatchResponse }) {
  return (
    <div
      role="status"
      className="space-y-2 rounded-md border border-success/30 bg-success/5 p-3 text-sm"
    >
      <p className="font-medium">
        {result.replayed ? 'Existing RFQ dispatch replayed' : 'RFQ dispatched'}
      </p>
      <dl className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
        <div>
          <dt className="inline font-medium">Event: </dt>
          <dd className="inline break-all font-mono">{result.event_id}</dd>
        </div>
        <div>
          <dt className="inline font-medium">Webhook status: </dt>
          <dd className="inline">{result.webhook_status}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="inline font-medium">Dispatch key: </dt>
          <dd className="inline break-all font-mono">{result.idempotency_key}</dd>
        </div>
      </dl>
      <p className="text-xs text-muted-foreground">
        Incoming supplier quote references will appear in the Supplier quotes panel after the n8n
        callback.
      </p>
    </div>
  )
}

export function DispatchRfqDialog({
  tender,
  onClose,
}: {
  tender: Tender
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [suppliers, setSuppliers] = React.useState<SupplierDraft[]>(() => [newSupplier()])
  const [responseDeadline, setResponseDeadline] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const [attempted, setAttempted] = React.useState(false)
  const [result, setResult] = React.useState<RfqDispatchResponse | null>(null)
  const firstSupplierRef = React.useRef<HTMLInputElement>(null)

  // Reopening the dialog with the same supplier/deadline/notes content must
  // reuse the idempotency key so a pending dispatch can be retried after TTL.
  const idempotencyKey = React.useMemo(() => {
    const canonical = JSON.stringify({
      tender_id: tender.id,
      suppliers: suppliers.map((supplier) => ({
        name: supplier.name.trim(),
        email: supplier.email.trim(),
        contact_name: (supplier.contact_name ?? '').trim(),
      })),
      response_deadline: responseDeadline,
      notes: notes.trim(),
    })
    return `rfq-${tender.id.slice(0, 8)}-${stableHash(canonical)}`
  }, [tender.id, suppliers, responseDeadline, notes])

  const dispatch = useMutation({
    mutationFn: (payload: RfqDispatchRequest) => dispatchRfq(payload, idempotencyKey),
    onSuccess: async (data) => {
      setResult(data)
      toast.success(data.replayed ? 'Existing RFQ dispatch replayed' : 'RFQ dispatched')
      await queryClient.invalidateQueries({ queryKey: ['supplier-quotes', tender.id] })
    },
  })

  const supplierErrors = suppliers.map((supplier) => ({
    name: supplier.name.trim().length === 0 ? 'Enter the supplier name.' : undefined,
    email: EMAIL_RE.test(supplier.email.trim())
      ? undefined
      : 'Enter a valid supplier e-mail address.',
  }))
  const deadlineError =
    responseDeadline.length > 0 &&
    Number.isNaN(Date.parse(`${responseDeadline}T00:00:00Z`))
      ? 'Choose a valid response deadline.'
      : undefined
  const hasErrors =
    supplierErrors.some((error) => error.name !== undefined || error.email !== undefined) ||
    deadlineError !== undefined

  const updateSupplier = (key: string, patch: Partial<RfqSupplier>): void => {
    setSuppliers((current) =>
      current.map((supplier) => (supplier.key === key ? { ...supplier, ...patch } : supplier)),
    )
  }

  const removeSupplier = (key: string): void => {
    setSuppliers((current) => (current.length > 1 ? current.filter((item) => item.key !== key) : current))
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (dispatch.isPending) {
      return
    }
    if (hasErrors) {
      firstSupplierRef.current?.focus()
      return
    }
    dispatch.mutate({
      tender_id: tender.id,
      suppliers: suppliers.map(({ key: _key, ...supplier }) => ({
        name: supplier.name.trim(),
        email: supplier.email.trim(),
        ...((supplier.contact_name ?? '').trim().length > 0
          ? { contact_name: (supplier.contact_name ?? '').trim() }
          : {}),
      })),
      ...(responseDeadline.length > 0
        ? { response_deadline: `${responseDeadline}T00:00:00Z` }
        : {}),
      ...(notes.trim().length > 0 ? { notes: notes.trim() } : {}),
    })
  }

  const failure = dispatch.error !== null ? errorCopy(dispatch.error) : null

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Dispatch RFQ for {tender.tender_number}</DialogTitle>
          <DialogDescription>
            Send a signed RFQ envelope through n8n. The same dispatch key is reused if you retry
            after a failure; pending reservations expire after the configured TTL.
          </DialogDescription>
        </DialogHeader>

        {result !== null ? (
          <div className="space-y-4">
            <DispatchResult result={result} />
            <DialogFooter>
              <Button type="button" onClick={onClose}>
                Close
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form className="space-y-4" noValidate onSubmit={handleSubmit}>
            {attempted && hasErrors ? (
              <p
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive"
              >
                Fix the supplier details before dispatching the RFQ.
              </p>
            ) : null}

            <fieldset className="space-y-3">
              <legend className="text-sm font-semibold">Suppliers</legend>
              <p className="text-xs text-muted-foreground">
                Each recipient receives the tender positions and a signed callback URL.
              </p>
              <div className="space-y-3">
                {suppliers.map((supplier, index) => {
                  const errors = supplierErrors[index] ?? { name: undefined, email: undefined }
                  const nameVisible = attempted && errors.name !== undefined
                  const emailVisible = attempted && errors.email !== undefined
                  return (
                    <div key={supplier.key} className="rounded-md border border-border p-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          Supplier {index + 1}
                        </p>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Remove supplier ${index + 1}`}
                          disabled={suppliers.length === 1 || dispatch.isPending}
                          onClick={() => removeSupplier(supplier.key)}
                        >
                          <Trash2 />
                          Remove
                        </Button>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-3">
                        <div className="space-y-1">
                          <Label htmlFor={`rfq-name-${supplier.key}`}>
                            Name <span aria-hidden="true">*</span>
                          </Label>
                          <Input
                            id={`rfq-name-${supplier.key}`}
                            ref={index === 0 ? firstSupplierRef : undefined}
                            name={`supplier-name-${index}`}
                            value={supplier.name}
                            onChange={(event) =>
                              updateSupplier(supplier.key, { name: event.target.value })
                            }
                            aria-invalid={nameVisible ? true : undefined}
                            aria-describedby={
                              nameVisible ? `rfq-name-${supplier.key}-error` : undefined
                            }
                            autoComplete="organization"
                            disabled={dispatch.isPending}
                          />
                          {nameVisible ? (
                            <p
                              id={`rfq-name-${supplier.key}-error`}
                              role="alert"
                              className="text-xs text-destructive"
                            >
                              {errors.name}
                            </p>
                          ) : null}
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor={`rfq-email-${supplier.key}`}>
                            E-mail <span aria-hidden="true">*</span>
                          </Label>
                          <Input
                            id={`rfq-email-${supplier.key}`}
                            name={`supplier-email-${index}`}
                            type="email"
                            value={supplier.email}
                            onChange={(event) =>
                              updateSupplier(supplier.key, { email: event.target.value })
                            }
                            aria-invalid={emailVisible ? true : undefined}
                            aria-describedby={
                              emailVisible ? `rfq-email-${supplier.key}-error` : undefined
                            }
                            autoComplete="email"
                            disabled={dispatch.isPending}
                          />
                          {emailVisible ? (
                            <p
                              id={`rfq-email-${supplier.key}-error`}
                              role="alert"
                              className="text-xs text-destructive"
                            >
                              {errors.email}
                            </p>
                          ) : null}
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor={`rfq-contact-${supplier.key}`}>Contact</Label>
                          <Input
                            id={`rfq-contact-${supplier.key}`}
                            name={`supplier-contact-${index}`}
                            value={supplier.contact_name ?? ''}
                            onChange={(event) =>
                              updateSupplier(supplier.key, { contact_name: event.target.value })
                            }
                            autoComplete="name"
                            disabled={dispatch.isPending}
                          />
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={dispatch.isPending}
                onClick={() => setSuppliers((current) => [...current, newSupplier()])}
              >
                <Plus />
                Add supplier
              </Button>
            </fieldset>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="rfq-deadline">Response deadline</Label>
                <p id="rfq-deadline-hint" className="text-xs text-muted-foreground">
                  Optional last day for supplier responses.
                </p>
                <Input
                  id="rfq-deadline"
                  name="responseDeadline"
                  type="date"
                  value={responseDeadline}
                  onChange={(event) => setResponseDeadline(event.target.value)}
                  aria-invalid={attempted && deadlineError !== undefined ? true : undefined}
                  aria-describedby={
                    attempted && deadlineError !== undefined
                      ? 'rfq-deadline-hint rfq-deadline-error'
                      : 'rfq-deadline-hint'
                  }
                  disabled={dispatch.isPending}
                />
                {attempted && deadlineError !== undefined ? (
                  <p id="rfq-deadline-error" role="alert" className="text-xs text-destructive">
                    {deadlineError}
                  </p>
                ) : null}
              </div>
              <div className="space-y-1 sm:col-span-2">
                <Label htmlFor="rfq-notes">Notes</Label>
                <p id="rfq-notes-hint" className="text-xs text-muted-foreground">
                  Optional instructions included in the signed envelope.
                </p>
                <Textarea
                  id="rfq-notes"
                  name="notes"
                  rows={3}
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  aria-describedby="rfq-notes-hint"
                  disabled={dispatch.isPending}
                />
              </div>
            </div>

            {failure !== null ? (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                <div>
                  <p className="text-sm font-medium text-destructive">{failure.title}</p>
                  <p className="text-xs text-muted-foreground">{failure.detail}</p>
                </div>
              </div>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose} disabled={dispatch.isPending}>
                Cancel
              </Button>
              <Button type="submit" disabled={dispatch.isPending}>
                {dispatch.isPending ? <Loader2 className="animate-spin" /> : <RadioTower />}
                Dispatch RFQ
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
