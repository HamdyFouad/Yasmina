import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ClipboardList, Plus, RadioTower, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/status-badge'
import { apiErrorMessage } from '@/lib/api-error'
import { acceptSupplierQuote, fetchSupplierQuotes } from '@/lib/sourcing-api'
import { formatCurrency, formatDate } from '@/lib/utils'
import type { SupplierQuote, Tender } from '@/types/domain'

function deliveryLabel(quote: SupplierQuote): string {
  const terms =
    quote.incoterms !== null && quote.incoterms.length > 0 ? quote.incoterms : 'No Incoterm'
  const days = quote.delivery_days !== null ? `${quote.delivery_days} day(s)` : 'No lead time'
  return `${terms} · ${days}`
}

function sourceLabel(quote: SupplierQuote): string {
  return quote.source === 'DASHBOARD' ? 'Dashboard' : 'n8n'
}

export function SupplierQuotePanel({
  tender,
  canEdit,
  onAddQuote,
  onDispatchRfq,
}: {
  tender: Tender
  canEdit: boolean
  onAddQuote: () => void
  onDispatchRfq: () => void
}) {
  const captionId = React.useId()
  const queryClient = useQueryClient()
  const quotes = useQuery({
    queryKey: ['supplier-quotes', tender.id],
    queryFn: () => fetchSupplierQuotes(tender.id, { limit: 50 }),
  })
  const acceptQuote = useMutation({
    mutationFn: (quoteId: string) => acceptSupplierQuote(quoteId),
    onSuccess: async (result) => {
      toast.success(`Supplier quote ${result.quote.quote_reference} accepted`, {
        description: 'Create the purchase order from the Procurement workspace.',
      })
      await queryClient.invalidateQueries({ queryKey: ['supplier-quotes', tender.id] })
      await queryClient.invalidateQueries({ queryKey: ['procurement', 'accepted-quotes'] })
      await queryClient.invalidateQueries({ queryKey: ['procurement', 'purchase-orders'] })
    },
  })
  const rows = quotes.data?.quotes ?? []

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="max-w-xl text-xs text-muted-foreground">
          Compare supplier prices, validity, delivery terms and the source of each quotation.
        </p>
        {canEdit ? (
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" onClick={onDispatchRfq}>
              <RadioTower />
              Dispatch RFQ
            </Button>
            <Button type="button" size="sm" onClick={onAddQuote}>
              <Plus />
              Add supplier quote
            </Button>
          </div>
        ) : null}
      </div>

      {acceptQuote.isError ? (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
          {apiErrorMessage(acceptQuote.error)}
        </p>
      ) : null}

      {quotes.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : quotes.isError ? (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-destructive">Could not load supplier quotes</p>
            <p className="text-xs text-muted-foreground">{apiErrorMessage(quotes.error)}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => void quotes.refetch()}
            >
              <RefreshCw />
              Try again
            </Button>
          </div>
        </div>
      ) : rows.length === 0 ? (
        <div className="flex items-start gap-3 rounded-md border border-dashed border-border p-3">
          <ClipboardList className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium">No supplier quotes yet</p>
            <p className="text-xs text-muted-foreground">
              Dispatch an RFQ or enter a quote manually. Incoming n8n callbacks appear here with
              their quote reference.
            </p>
          </div>
        </div>
      ) : (
        <div
          role="region"
          aria-labelledby={captionId}
          tabIndex={0}
          className="stacked-table max-h-80 overflow-auto rounded-md border border-border focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <table className="w-full min-w-[900px] border-collapse text-sm">
            <caption id={captionId} className="sr-only">
              Supplier quotes with price, validity, delivery terms, source and line coverage
            </caption>
            <thead className="sticky top-0 z-10 bg-card">
              <tr className="border-b border-border">
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium uppercase">
                  Supplier / reference
                </th>
                <th scope="col" className="px-3 py-2 text-right text-xs font-medium uppercase">
                  Total net
                </th>
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium uppercase">
                  Valid until
                </th>
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium uppercase">
                  Delivery
                </th>
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium uppercase">
                  Source
                </th>
                <th scope="col" className="px-3 py-2 text-right text-xs font-medium uppercase">
                  Lines
                </th>
                <th scope="col" className="px-3 py-2 text-left text-xs font-medium uppercase">
                  Status
                </th>
                <th scope="col" className="px-3 py-2 text-right text-xs font-medium uppercase">
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((quote) => (
                <tr key={quote.id} className="border-b border-border last:border-0">
                  <th scope="row" className="max-w-64 px-3 py-2 text-left align-top">
                    <span className="block truncate font-medium">{quote.supplier_name}</span>
                    <span className="block truncate font-mono text-[11px] text-muted-foreground">
                      {quote.quote_reference}
                    </span>
                  </th>
                  <td
                    data-label="Total net"
                    className="whitespace-nowrap px-3 py-2 text-right align-top font-medium"
                  >
                    {quote.total_net !== null
                      ? formatCurrency(quote.total_net, quote.currency)
                      : '—'}
                  </td>
                  <td data-label="Valid until" className="whitespace-nowrap px-3 py-2 align-top">
                    {quote.valid_until !== null ? formatDate(quote.valid_until) : '—'}
                  </td>
                  <td data-label="Delivery" className="px-3 py-2 align-top text-xs">
                    {deliveryLabel(quote)}
                  </td>
                  <td data-label="Source" className="px-3 py-2 align-top">
                    <Badge variant={quote.source === 'DASHBOARD' ? 'secondary' : 'default'}>
                      {sourceLabel(quote)}
                    </Badge>
                  </td>
                  <td
                    data-label="Lines"
                    className="whitespace-nowrap px-3 py-2 text-right align-top"
                  >
                    {quote.line_count.toLocaleString('de-DE')}
                  </td>
                  <td data-label="Status" className="px-3 py-2 align-top">
                    <StatusBadge status={quote.status} />
                  </td>
                  <td data-label="Action" className="whitespace-nowrap px-3 py-2 text-right align-top">
                    {canEdit &&
                    quote.status !== 'ACCEPTED' &&
                    quote.status !== 'REJECTED' &&
                    quote.status !== 'EXPIRED' ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={acceptQuote.isPending}
                        aria-label={`Accept quote for ${quote.supplier_name} ${quote.quote_reference}`}
                        onClick={() => acceptQuote.mutate(quote.id)}
                      >
                        <CheckCircle2 />
                        Accept quote
                      </Button>
                    ) : quote.status === 'ACCEPTED' ? (
                      <span className="text-xs font-medium text-success">Ready for PO</span>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
