import * as React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ClipboardCheck, Loader2 } from 'lucide-react'
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
import { Textarea } from '@/components/ui/textarea'
import { apiErrorMessage } from '@/lib/api-error'
import { createPurchaseOrder, fetchAcceptedSupplierQuotes } from '@/lib/procurement-api'
import { formatCurrency, formatDate } from '@/lib/utils'
import type { PurchaseOrder } from '@/types/domain'

function dateAfterDays(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

export function CreatePurchaseOrderDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (purchaseOrder: PurchaseOrder) => void
}) {
  const [quoteId, setQuoteId] = React.useState('')
  const [expectedDeliveryDate, setExpectedDeliveryDate] = React.useState(dateAfterDays(14))
  const [notes, setNotes] = React.useState('')
  const [attempted, setAttempted] = React.useState(false)
  const quoteRef = React.useRef<HTMLButtonElement>(null)

  const quotes = useQuery({
    queryKey: ['procurement', 'accepted-quotes'],
    queryFn: fetchAcceptedSupplierQuotes,
  })
  const selectedQuote = quotes.data?.quotes.find((quote) => quote.id === quoteId)
  const quoteError = quoteId.length === 0 ? 'Choose an accepted supplier quote.' : null

  const mutation = useMutation({
    mutationFn: () =>
      createPurchaseOrder({
        supplier_quote_id: quoteId,
        expected_delivery_date: expectedDeliveryDate || null,
        notes: notes.trim() || null,
      }),
    onSuccess: (purchaseOrder) => {
      toast.success('Purchase order created', {
        description: `${purchaseOrder.po_number} · ${purchaseOrder.supplier_name}`,
      })
      onCreated(purchaseOrder)
      onClose()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (mutation.isPending) return
    setAttempted(true)
    if (quoteError !== null) return
    mutation.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        className="sm:max-w-2xl"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          quoteRef.current?.focus()
        }}
      >
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Create purchase order</DialogTitle>
            <DialogDescription>
              Generate a draft PO from an accepted supplier quote. The quote must be accepted in the
              tender workspace first.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1">
            <Label htmlFor="po-quote">Accepted supplier quote</Label>
            <p id="po-quote-hint" className="text-xs text-muted-foreground">
              Only accepted quotes without an existing purchase order are listed.
            </p>
            <Select value={quoteId} onValueChange={setQuoteId}>
              <SelectTrigger
                id="po-quote"
                ref={quoteRef}
                aria-describedby="po-quote-hint"
                aria-invalid={attempted && quoteError !== null ? true : undefined}
              >
                <SelectValue placeholder="Select an accepted quote" />
              </SelectTrigger>
              <SelectContent>
                {(quotes.data?.quotes ?? []).map((quote) => (
                  <SelectItem key={quote.id} value={quote.id}>
                    {quote.supplier_name} · {quote.quote_reference}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {attempted && quoteError !== null ? (
              <p role="alert" className="text-xs text-destructive">
                {quoteError}
              </p>
            ) : null}
            {quotes.isLoading ? (
              <p className="text-xs text-muted-foreground">Loading accepted quotes…</p>
            ) : null}
            {quotes.isError ? (
              <p role="alert" className="text-xs text-destructive">
                {apiErrorMessage(quotes.error)}
              </p>
            ) : null}
            {quotes.data !== undefined && quotes.data.quotes.length === 0 ? (
              <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                No accepted quotes are ready. Accept a supplier quote in the tender workspace, then
                return here.
              </p>
            ) : null}
          </div>

          {selectedQuote !== undefined ? (
            <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="font-medium">{selectedQuote.supplier_name}</p>
                  <p className="text-xs text-muted-foreground">
                    {selectedQuote.quote_reference} · {selectedQuote.line_count} line(s)
                  </p>
                </div>
                <p className="font-medium">
                  {selectedQuote.total_net !== null
                    ? formatCurrency(selectedQuote.total_net, selectedQuote.currency)
                    : '—'}
                </p>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                Delivery terms: {selectedQuote.delivery_days ?? '—'} day(s) · Valid until{' '}
                {formatDate(selectedQuote.valid_until)}
              </p>
            </div>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="po-expected-delivery">Expected delivery</Label>
              <Input
                id="po-expected-delivery"
                type="date"
                min={dateAfterDays(0)}
                value={expectedDeliveryDate}
                onChange={(event) => setExpectedDeliveryDate(event.target.value)}
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="po-notes">Notes</Label>
              <Textarea
                id="po-notes"
                rows={3}
                maxLength={4000}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </div>
          </div>

          {mutation.isError ? (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
              {apiErrorMessage(mutation.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending || quotes.isLoading}>
              {mutation.isPending ? <Loader2 className="animate-spin" /> : <ClipboardCheck />}
              Create PO
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
