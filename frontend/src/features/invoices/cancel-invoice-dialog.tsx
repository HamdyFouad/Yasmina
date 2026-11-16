import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Loader2 } from 'lucide-react'
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
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { apiErrorMessage } from '@/lib/api-error'
import { cancelInvoice } from '@/lib/invoices-api'
import type { Invoice, InvoiceCancelResponse, InvoiceDetailResponse } from '@/types/domain'

export function CancelInvoiceDialog({
  invoice,
  onClose,
  onCancelled,
}: {
  invoice: Invoice
  onClose: () => void
  onCancelled: (response: InvoiceCancelResponse) => void
}) {
  const queryClient = useQueryClient()
  const reasonRef = React.useRef<HTMLTextAreaElement>(null)
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  const [reason, setReason] = React.useState('')
  const [attempted, setAttempted] = React.useState(false)
  const reasonError =
    attempted && reason.trim().length < 3
      ? 'Give a cancellation reason with at least 3 characters.'
      : undefined

  const cancel = useMutation({
    mutationFn: () => cancelInvoice(invoice.id, { reason: reason.trim() }),
    onSuccess: async (response) => {
      toast.success('Invoice cancelled', { description: invoice.invoice_number })
      queryClient.setQueryData<InvoiceDetailResponse>(
        ['invoices', invoice.id],
        (current) =>
          current === undefined
            ? current
            : { ...current, invoice: response.invoice, events: response.events },
      )
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['invoices'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
      onCancelled(response)
      onClose()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (cancel.isPending) {
      return
    }
    if (reason.trim().length < 3) {
      reasonRef.current?.focus()
      return
    }
    cancel.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          cancelRef.current?.focus()
        }}
      >
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Cancel {invoice.invoice_number}?</DialogTitle>
            <DialogDescription>
              This permanently moves the invoice to <strong>Cancelled</strong>. Use a credit
              note instead when a payment or credit has already been recorded.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <Label htmlFor="cancel-reason">Cancellation reason</Label>
            <p id="cancel-reason-hint" className="text-xs text-muted-foreground">
              Stored in the GoBD invoice event log.
            </p>
            <Textarea
              id="cancel-reason"
              ref={reasonRef}
              name="reason"
              rows={3}
              maxLength={512}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              aria-invalid={reasonError !== undefined ? true : undefined}
              aria-describedby={
                reasonError !== undefined
                  ? 'cancel-reason-hint cancel-reason-error'
                  : 'cancel-reason-hint'
              }
              disabled={cancel.isPending}
            />
            {reasonError !== undefined ? (
              <p id="cancel-reason-error" role="alert" className="text-xs text-destructive">
                {reasonError}
              </p>
            ) : null}
          </div>

          {cancel.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {apiErrorMessage(cancel.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              ref={cancelRef}
              type="button"
              variant="outline"
              disabled={cancel.isPending}
              onClick={onClose}
            >
              Keep invoice
            </Button>
            <Button type="submit" variant="destructive" disabled={cancel.isPending}>
              {cancel.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <Ban aria-hidden="true" />
              )}
              Cancel invoice
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
