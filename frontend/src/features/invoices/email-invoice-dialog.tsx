import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Mail } from 'lucide-react'
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
import { apiErrorMessage } from '@/lib/api-error'
import { emailInvoice } from '@/lib/invoices-api'
import type { Invoice, InvoiceDetailResponse, InvoiceEmailResponse } from '@/types/domain'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function EmailInvoiceDialog({
  invoice,
  onClose,
  onSent,
}: {
  invoice: Invoice
  onClose: () => void
  onSent: (response: InvoiceEmailResponse) => void
}) {
  const queryClient = useQueryClient()
  const [recipient, setRecipient] = React.useState(invoice.buyer_email ?? '')
  const [attempted, setAttempted] = React.useState(false)
  const inputRef = React.useRef<HTMLInputElement>(null)
  const recipientError =
    attempted && recipient.trim().length > 0 && !EMAIL_RE.test(recipient.trim())
      ? 'Enter a valid e-mail address.'
      : undefined

  const send = useMutation({
    mutationFn: () =>
      emailInvoice(invoice.id, {
        ...(recipient.trim() ? { recipient: recipient.trim() } : {}),
      }),
    onSuccess: async (response) => {
      toast.success('Invoice sent', { description: response.delivery.recipient })
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
      onSent(response)
      onClose()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (send.isPending) {
      return
    }
    if (recipientError !== undefined) {
      inputRef.current?.focus()
      return
    }
    send.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Send {invoice.invoice_number} by e-mail</DialogTitle>
            <DialogDescription>
              The buyer receives the ZUGFeRD PDF and XRechnung XML as attachments. Delivery is
              recorded in the GoBD event log.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <Label htmlFor="invoice-email-recipient">Recipient e-mail</Label>
            <p id="invoice-email-hint" className="text-xs text-muted-foreground">
              Leave empty to use the buyer address on file.
            </p>
            <Input
              id="invoice-email-recipient"
              ref={inputRef}
              name="recipient"
              type="email"
              autoComplete="email"
              autoFocus
              value={recipient}
              onChange={(event) => setRecipient(event.target.value)}
              aria-invalid={recipientError !== undefined ? true : undefined}
              aria-describedby={
                recipientError !== undefined
                  ? 'invoice-email-hint invoice-email-error'
                  : 'invoice-email-hint'
              }
              disabled={send.isPending}
            />
            {recipientError !== undefined ? (
              <p id="invoice-email-error" role="alert" className="text-xs text-destructive">
                {recipientError}
              </p>
            ) : null}
          </div>

          {send.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {apiErrorMessage(send.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={send.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={send.isPending}>
              {send.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <Mail aria-hidden="true" />
              )}
              Send invoice
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
