import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2 } from 'lucide-react'
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
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage, isApiError } from '@/lib/api-error'
import { approveOffer } from '@/lib/offers-api'
import { formatCurrency } from '@/lib/utils'
import type {
  ApproveOfferPayload,
  ApproveOfferResponse,
  CustomerOffer,
} from '@/types/domain'
import {
  INVOICE_FIELD_ORDER,
  InvoiceFields,
  createInvoiceForm,
  toInvoicePayload,
  validateInvoiceForm,
} from '@/features/offers/invoice-fields'
import type { InvoiceField, InvoiceFormValues } from '@/features/offers/invoice-fields'

function approvalErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 403) {
    return 'Four-eyes policy: the offer owner cannot approve their own offer. A different approver must sign it.'
  }
  return apiErrorMessage(error)
}

function isInvoiceGenerationFailure(
  error: unknown,
  variables: ApproveOfferPayload | undefined,
): boolean {
  if (variables?.invoice === undefined || !isApiError(error) || error.status !== 422) {
    return false
  }
  return error.detail.includes('e-invoice generation failed')
}

function artifactSummary(artifacts: ApproveOfferResponse['artifacts']): string | null {
  if (artifacts === null) {
    return null
  }
  const labels: Record<string, string> = { xrechnung: 'XRechnung', zugferd: 'ZUGFeRD' }
  const parts = Object.entries(artifacts).map(
    ([key, artifact]) =>
      `${labels[key] ?? key}: ${artifact.object_name} (${artifact.download_url})`,
  )
  return parts.length > 0 ? parts.join(' · ') : null
}

export function ApprovalDialog({
  offer,
  onClose,
}: {
  offer: CustomerOffer
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const formRef = React.useRef<HTMLFormElement>(null)
  const [invoiceEnabled, setInvoiceEnabled] = React.useState(false)
  const [values, setValues] = React.useState<InvoiceFormValues>(() => createInvoiceForm(offer))
  const [touched, setTouched] = React.useState<ReadonlySet<InvoiceField>>(new Set())
  const [attempted, setAttempted] = React.useState(false)

  const errors = validateInvoiceForm(values)

  const approve = useMutation({
    mutationFn: (payload: ApproveOfferPayload) => approveOffer(payload),
    onSuccess: async (result) => {
      const outcome =
        result.erpnext.status === 'queued'
          ? 'signed and queued for ERPNext'
          : result.erpnext.status === 'skipped'
            ? 'signed (ERPNext is not configured)'
            : 'signed'
      const invoiceSummary = artifactSummary(result.artifacts)
      if (invoiceSummary !== null) {
        toast.success(`Offer ${offer.offer_number} ${outcome}`, {
          description: invoiceSummary,
        })
      } else {
        toast.success(`Offer ${offer.offer_number} ${outcome}`)
      }
      onClose()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['offers'] }),
        queryClient.invalidateQueries({ queryKey: ['offer', offer.id] }),
        queryClient.invalidateQueries({ queryKey: ['invoices'] }),
        queryClient.invalidateQueries({ queryKey: ['ops'] }),
      ])
    },
    onError: async (error, payload) => {
      if (isInvoiceGenerationFailure(error, payload)) {
        // The approval itself is persisted (and an invoice row may already exist);
        // refresh the list/detail/invoice views even though artifacts failed.
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['offers'] }),
          queryClient.invalidateQueries({ queryKey: ['offer', offer.id] }),
          queryClient.invalidateQueries({ queryKey: ['invoices'] }),
          queryClient.invalidateQueries({ queryKey: ['ops'] }),
        ])
      }
    },
  })

  const isApprovingOwnOffer = offer.owner !== null && offer.owner === user?.subject

  const close = (): void => {
    approve.reset()
    onClose()
  }

  const handleChange = (field: InvoiceField, value: string): void => {
    setValues((current) => ({ ...current, [field]: value }))
  }

  const handleBlur = (field: InvoiceField): void => {
    setTouched((current) => {
      const next = new Set(current)
      next.add(field)
      return next
    })
  }

  const focusFirstInvalid = (): void => {
    const first = INVOICE_FIELD_ORDER.find((field) => errors[field] !== undefined)
    if (first === undefined) {
      return
    }
    formRef.current?.querySelector<HTMLElement>(`#invoice-${first}`)?.focus()
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (approve.isPending) {
      return
    }
    if (invoiceEnabled) {
      setAttempted(true)
      if (Object.keys(errors).length > 0) {
        focusFirstInvalid()
        return
      }
      approve.mutate({ offer, invoice: toInvoicePayload(values) })
      return
    }
    approve.mutate({ offer })
  }

  const invoiceFailure = isInvoiceGenerationFailure(approve.error, approve.variables)

  return (
    <Dialog open onOpenChange={(open) => (!open ? close() : undefined)}>
      <DialogContent>
        <form ref={formRef} noValidate className="space-y-4" onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Approve offer {offer.offer_number}?</DialogTitle>
            <DialogDescription>
              The server signs the canonical offer payload with the Ed25519 custody key and, when
              ERPNext is configured, queues the quotation for the background outbox. You can
              optionally issue the e-invoice in the same step.
            </DialogDescription>
          </DialogHeader>

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
              <dd>{formatCurrency(offer.total_gross, offer.currency)}</dd>
            </div>
          </dl>

          {isApprovingOwnOffer ? (
            <p className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs text-foreground">
              You own this offer. The four-eyes policy will reject this approval; a different
              approver must sign it.
            </p>
          ) : null}

          <InvoiceFields
            enabled={invoiceEnabled}
            onToggle={setInvoiceEnabled}
            values={values}
            errors={errors}
            touched={touched}
            attempted={attempted}
            onChange={handleChange}
            onBlur={handleBlur}
          />

          {approve.isError ? (
            <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2">
              {invoiceFailure ? (
                <>
                  <p className="text-sm font-medium text-destructive">
                    The offer was signed and saved, but the e-invoice could not be generated.
                  </p>
                  <p className="text-xs text-muted-foreground">{apiErrorMessage(approve.error)}</p>
                  <p className="text-xs text-muted-foreground">
                    The offer is now approved; submit again to retry the invoice, or cancel and
                    retry later.
                  </p>
                </>
              ) : (
                <p className="text-sm text-destructive">{approvalErrorMessage(approve.error)}</p>
              )}
            </div>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              autoFocus
              disabled={approve.isPending}
              onClick={close}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={approve.isPending}>
              {approve.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <CheckCircle2 aria-hidden="true" />
              )}
              {invoiceEnabled ? 'Approve, sign and issue invoice' : 'Approve and sign'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
