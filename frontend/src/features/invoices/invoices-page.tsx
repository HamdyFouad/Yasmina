import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  CreditCard,
  Download,
  FileDown,
  Loader2,
  ReceiptText,
  RefreshCw,
} from 'lucide-react'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
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
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import {
  downloadInvoiceDocument,
  fetchInvoices,
  recordInvoicePayment,
} from '@/lib/invoices-api'
import { formatCurrency, formatDate } from '@/lib/utils'
import type { Invoice, InvoiceDocumentFormat, InvoiceStatus } from '@/types/domain'

const PAGE_SIZE = 10

const STATUS_OPTIONS: { value: InvoiceStatus | 'ALL'; label: string }[] = [
  { value: 'ALL', label: 'All statuses' },
  { value: 'DRAFT', label: 'Draft' },
  { value: 'ISSUED', label: 'Issued' },
  { value: 'PARTIALLY_PAID', label: 'Partially paid' },
  { value: 'PAID', label: 'Paid' },
  { value: 'OVERDUE', label: 'Overdue' },
  { value: 'PARTIALLY_CREDITED', label: 'Partially credited' },
  { value: 'CREDITED', label: 'Credited' },
  { value: 'CANCELLED', label: 'Cancelled' },
]

function isInvoiceStatus(value: string): value is InvoiceStatus {
  return STATUS_OPTIONS.some((option) => option.value === value && option.value !== 'ALL')
}

function remainingAmount(invoice: Invoice): number {
  const gross = Number.parseFloat(invoice.total_gross)
  const paid = Number.parseFloat(invoice.paid_amount)
  const credited = Number.parseFloat(invoice.credited_amount)
  if (Number.isNaN(gross)) {
    return 0
  }
  const remaining = gross - (Number.isNaN(paid) ? 0 : paid) - (Number.isNaN(credited) ? 0 : credited)
  return remaining > 0 ? Math.round(remaining * 100) / 100 : 0
}

function PaymentDialog({
  invoice,
  onClose,
}: {
  invoice: Invoice
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [amount, setAmount] = React.useState(() => remainingAmount(invoice).toFixed(2))
  const [reference, setReference] = React.useState('')
  const [validationError, setValidationError] = React.useState<string | null>(null)

  const payment = useMutation({
    mutationFn: (payload: { amount: string; reference?: string }) =>
      recordInvoicePayment(invoice.id, payload),
    onSuccess: async () => {
      toast.success('Payment recorded', {
        description: `${invoice.invoice_number} was updated.`,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['invoices'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
      onClose()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    const parsed = Number.parseFloat(amount)
    if (Number.isNaN(parsed) || parsed <= 0) {
      setValidationError('Enter a positive payment amount.')
      return
    }
    const open = remainingAmount(invoice)
    if (parsed > open + 0.005) {
      setValidationError(`The open amount is ${formatCurrency(open.toFixed(2), invoice.currency)}.`)
      return
    }
    setValidationError(null)
    payment.mutate({
      amount: parsed.toFixed(2),
      ...(reference.trim().length > 0 ? { reference: reference.trim() } : {}),
    })
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Mark invoice as paid</DialogTitle>
          <DialogDescription>
            {invoice.invoice_number} · open amount{' '}
            {formatCurrency(remainingAmount(invoice).toFixed(2), invoice.currency)}
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="space-y-1.5">
            <Label htmlFor="payment-amount">Amount ({invoice.currency})</Label>
            <Input
              id="payment-amount"
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              disabled={payment.isPending}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="payment-reference">Bank reference (optional)</Label>
            <Input
              id="payment-reference"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              placeholder="e.g. BANK-2026-0042"
              disabled={payment.isPending}
            />
          </div>
          {validationError !== null ? (
            <p role="alert" className="text-sm text-destructive">
              {validationError}
            </p>
          ) : null}
          {payment.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {apiErrorMessage(payment.error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={payment.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={payment.isPending}>
              {payment.isPending ? <Loader2 className="animate-spin" /> : <CreditCard />}
              Record payment
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function InvoicesPage() {
  const { hasRole } = useAuth()
  const [status, setStatus] = React.useState<InvoiceStatus | null>(null)
  const [page, setPage] = React.useState(0)
  const [downloading, setDownloading] = React.useState<string | null>(null)
  const [payingInvoice, setPayingInvoice] = React.useState<Invoice | null>(null)

  const canMarkPaid = hasRole('admin')
  const offset = page * PAGE_SIZE
  const invoices = useQuery({
    queryKey: ['invoices', { status, offset }],
    queryFn: () =>
      fetchInvoices({
        limit: PAGE_SIZE,
        offset,
        ...(status !== null ? { status } : {}),
      }),
  })

  const total = invoices.data?.count ?? 0
  const rows = invoices.data?.invoices ?? []
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = offset + rows.length

  const handleStatusChange = (value: string): void => {
    setStatus(isInvoiceStatus(value) ? value : null)
    setPage(0)
  }

  const handleDownload = async (
    invoice: Invoice,
    format: InvoiceDocumentFormat,
  ): Promise<void> => {
    const key = `${invoice.id}:${format}`
    setDownloading(key)
    try {
      await downloadInvoiceDocument(
        invoice.id,
        format,
        `${invoice.invoice_number}.${format === 'zugferd' ? 'pdf' : 'xml'}`,
      )
      toast.success('Invoice document downloaded')
    } catch (error) {
      toast.error('Download failed', { description: apiErrorMessage(error) })
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Invoices & billing"
        description="Paginated invoices with GoBD status, totals and compliant document downloads."
        actions={
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={invoices.isFetching}
            onClick={() => void invoices.refetch()}
          >
            {invoices.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Refresh
          </Button>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="invoice-status" className="text-sm text-muted-foreground">
            Status
          </label>
          <Select value={status ?? 'ALL'} onValueChange={handleStatusChange}>
            <SelectTrigger id="invoice-status" className="w-48">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <p className="text-xs text-muted-foreground">
          {total.toLocaleString('de-DE')} invoice(s) visible to your role
        </p>
      </div>

      {invoices.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : invoices.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <AlertTriangle className="h-6 w-6 text-destructive" />
            <div>
              <p className="text-sm font-medium">Could not load invoices</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(invoices.error)}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void invoices.refetch()}>
              <RefreshCw />
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={ReceiptText}
          title="No invoices found"
          description={
            status === null
              ? 'Invoices are created from approved customer offers. Approve an offer to issue the first invoice.'
              : 'No invoices match the selected status filter.'
          }
          hint="Every invoice carries a gapless number and a hash-chained event log."
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Invoice</TableHead>
                <TableHead>Issued / due</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Net</TableHead>
                <TableHead className="text-right">VAT</TableHead>
                <TableHead className="text-right">Gross</TableHead>
                <TableHead className="text-right">Paid</TableHead>
                <TableHead>Documents</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((invoice) => {
                const open = remainingAmount(invoice)
                const paidUp =
                  invoice.status === 'PAID' ||
                  invoice.status === 'CREDITED' ||
                  invoice.status === 'CANCELLED'
                return (
                  <TableRow key={invoice.id}>
                    <TableCell>
                      <div className="font-mono text-sm font-medium">
                        {invoice.invoice_number}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {invoice.vat_breakdown.length} VAT rate(s)
                      </div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      <div>{formatDate(invoice.issue_date)}</div>
                      <div className="text-xs text-muted-foreground">
                        {formatDate(invoice.due_date)}
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={invoice.status} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right">
                      {formatCurrency(invoice.total_net, invoice.currency)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right">
                      {formatCurrency(invoice.total_vat, invoice.currency)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right font-medium">
                      {formatCurrency(invoice.total_gross, invoice.currency)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right">
                      {formatCurrency(invoice.paid_amount, invoice.currency)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          title="Download ZUGFeRD PDF/A-3"
                          disabled={downloading !== null}
                          onClick={() => void handleDownload(invoice, 'zugferd')}
                        >
                          {downloading === `${invoice.id}:zugferd` ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <Download />
                          )}
                          PDF
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          title="Download XRechnung XML"
                          disabled={downloading !== null}
                          onClick={() => void handleDownload(invoice, 'xrechnung')}
                        >
                          {downloading === `${invoice.id}:xrechnung` ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <FileDown />
                          )}
                          XML
                        </Button>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      {canMarkPaid && !paidUp && open > 0 ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => setPayingInvoice(invoice)}
                        >
                          <CreditCard />
                          Mark as paid
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {paidUp ? 'Closed' : 'Admin only'}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {total === 0 ? 'No results' : `Showing ${rangeStart}–${rangeEnd} of ${total}`}
        </p>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page === 0 || invoices.isFetching}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
          >
            <ChevronLeft />
            Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {page + 1} of {pageCount}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page + 1 >= pageCount || invoices.isFetching}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
            <ChevronRight />
          </Button>
        </div>
      </div>

      {payingInvoice !== null ? (
        <PaymentDialog
          key={payingInvoice.id}
          invoice={payingInvoice}
          onClose={() => setPayingInvoice(null)}
        />
      ) : null}
    </div>
  )
}
