import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, RotateCcw } from 'lucide-react'
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
import { invoiceOutstanding, parseMoneyInput } from '@/lib/invoice-utils'
import { issueInvoiceCredit } from '@/lib/invoices-api'
import { formatCurrency } from '@/lib/utils'
import type {
  Invoice,
  InvoiceCreditRequest,
  InvoiceCreditResponse,
  InvoiceDetailResponse,
} from '@/types/domain'

function CreditNoteDialog({
  invoice,
  onClose,
}: {
  invoice: Invoice
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const amountRef = React.useRef<HTMLInputElement>(null)
  const reasonRef = React.useRef<HTMLTextAreaElement>(null)
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  const outstanding = invoiceOutstanding(invoice)
  const [amount, setAmount] = React.useState(() => outstanding.toFixed(2))
  const [reason, setReason] = React.useState('')
  const [touched, setTouched] = React.useState<ReadonlySet<'amount' | 'reason'>>(new Set())
  const [attempted, setAttempted] = React.useState(false)

  const parsedAmount = parseMoneyInput(amount)
  const amountError =
    parsedAmount === null
      ? 'Enter a positive amount with at most two decimal places.'
      : parsedAmount > outstanding + 0.005
        ? `The outstanding amount is ${formatCurrency(outstanding.toFixed(2), invoice.currency)}.`
        : undefined
  const reasonError =
    reason.trim().length < 3 ? 'Give a reason with at least 3 characters.' : undefined
  const visibleAmountError =
    attempted || touched.has('amount') ? amountError : undefined
  const visibleReasonError =
    attempted || touched.has('reason') ? reasonError : undefined

  const issue = useMutation({
    mutationFn: (payload: InvoiceCreditRequest) => issueInvoiceCredit(invoice.id, payload),
    onSuccess: async (data: InvoiceCreditResponse) => {
      toast.success(`Credit note ${data.credit_note.credit_note_number} issued`, {
        description: `${formatCurrency(data.credit_note.total_gross, invoice.currency)} credited to ${invoice.invoice_number}.`,
      })
      const updatedInvoice = data.invoice
      if (updatedInvoice !== null) {
        queryClient.setQueryData<InvoiceDetailResponse>(
          ['invoices', invoice.id],
          (current) =>
            current === undefined
              ? current
              : { ...current, invoice: updatedInvoice, events: data.events },
        )
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['invoices'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
      onClose()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (issue.isPending) {
      return
    }
    if (amountError !== undefined) {
      amountRef.current?.focus()
      return
    }
    if (reasonError !== undefined) {
      reasonRef.current?.focus()
      return
    }
    if (parsedAmount === null) {
      return
    }
    issue.mutate({
      amount: parsedAmount.toFixed(2),
      reason: reason.trim(),
    })
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
            <DialogTitle>Issue credit note for {invoice.invoice_number}</DialogTitle>
            <DialogDescription>
              Outstanding amount{' '}
              {formatCurrency(outstanding.toFixed(2), invoice.currency)}. The credit note is
              allocated across the invoice VAT rates and appended to the GoBD event log.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <Label htmlFor="credit-amount">Credit amount ({invoice.currency})</Label>
            <p id="credit-amount-hint" className="text-xs text-muted-foreground">
              Positive amount up to the outstanding balance, with at most two decimals.
            </p>
            <Input
              id="credit-amount"
              ref={amountRef}
              name="amount"
              inputMode="decimal"
              autoComplete="off"
              maxLength={16}
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              onBlur={() => setTouched((current) => new Set(current).add('amount'))}
              aria-invalid={visibleAmountError !== undefined ? true : undefined}
              aria-describedby={
                visibleAmountError !== undefined
                  ? 'credit-amount-hint credit-amount-error'
                  : 'credit-amount-hint'
              }
              disabled={issue.isPending}
            />
            {visibleAmountError !== undefined ? (
              <p id="credit-amount-error" role="alert" className="text-xs text-destructive">
                {visibleAmountError}
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="credit-reason">Reason</Label>
            <p id="credit-reason-hint" className="text-xs text-muted-foreground">
              Explain the correction; it is stored with the credit note and audit trail.
            </p>
            <Textarea
              id="credit-reason"
              ref={reasonRef}
              name="reason"
              rows={3}
              maxLength={4000}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              onBlur={() => setTouched((current) => new Set(current).add('reason'))}
              aria-invalid={visibleReasonError !== undefined ? true : undefined}
              aria-describedby={
                visibleReasonError !== undefined
                  ? 'credit-reason-hint credit-reason-error'
                  : 'credit-reason-hint'
              }
              disabled={issue.isPending}
            />
            {visibleReasonError !== undefined ? (
              <p id="credit-reason-error" role="alert" className="text-xs text-destructive">
                {visibleReasonError}
              </p>
            ) : null}
          </div>

          {issue.isError ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive"
            >
              {apiErrorMessage(issue.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              ref={cancelRef}
              type="button"
              variant="outline"
              disabled={issue.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={issue.isPending}>
              {issue.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw aria-hidden="true" />
              )}
              Issue credit note
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export { CreditNoteDialog }
