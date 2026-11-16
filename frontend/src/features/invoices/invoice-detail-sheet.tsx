import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Download,
  FileDown,
  Loader2,
  ReceiptText,
  RefreshCw,
  RotateCcw,
} from 'lucide-react'
import { toast } from 'sonner'

import { EventTimeline } from '@/components/event-timeline'
import { StatusBadge } from '@/components/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/features/auth/auth-context'
import { CreditNoteDialog } from '@/features/invoices/credit-note-dialog'
import { apiErrorMessage } from '@/lib/api-error'
import { invoiceOutstanding } from '@/lib/invoice-utils'
import {
  downloadInvoiceDocument,
  fetchInvoice,
  fetchInvoiceCreditNotes,
} from '@/lib/invoices-api'
import { formatCurrency, formatDate, formatDateTime, formatPercent, titleCase } from '@/lib/utils'
import type { Invoice, InvoiceDocumentFormat } from '@/types/domain'

function SummaryItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-foreground">{children}</dd>
    </div>
  )
}

function AmountCard({
  label,
  value,
  emphasis = false,
}: {
  label: string
  value: string
  emphasis?: boolean
}) {
  return (
    <div
      className={
        emphasis
          ? 'rounded-md border border-primary/30 bg-primary/5 p-3'
          : 'rounded-md border border-border bg-background p-3'
      }
    >
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-base font-semibold tabular-nums text-foreground">{value}</dd>
    </div>
  )
}

function InvoiceDocuments({ invoice }: { invoice: Invoice }) {
  const [downloading, setDownloading] = React.useState<InvoiceDocumentFormat | null>(null)

  const handleDownload = async (format: InvoiceDocumentFormat): Promise<void> => {
    setDownloading(format)
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
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={downloading !== null}
        onClick={() => void handleDownload('zugferd')}
      >
        {downloading === 'zugferd' ? (
          <Loader2 className="animate-spin" aria-hidden="true" />
        ) : (
          <Download aria-hidden="true" />
        )}
        ZUGFeRD PDF
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={downloading !== null}
        onClick={() => void handleDownload('xrechnung')}
      >
        {downloading === 'xrechnung' ? (
          <Loader2 className="animate-spin" aria-hidden="true" />
        ) : (
          <FileDown aria-hidden="true" />
        )}
        XRechnung XML
      </Button>
    </div>
  )
}

function InvoiceDetailBody({ invoiceId }: { invoiceId: string }) {
  const { hasRole } = useAuth()
  const canIssueCredit = hasRole('admin')
  const [creditOpen, setCreditOpen] = React.useState(false)

  const detail = useQuery({
    queryKey: ['invoices', invoiceId],
    queryFn: () => fetchInvoice(invoiceId),
  })
  const credits = useQuery({
    queryKey: ['invoices', invoiceId, 'credit-notes'],
    queryFn: () => fetchInvoiceCreditNotes(invoiceId),
  })

  const invoice = detail.data?.invoice
  const events = detail.data?.events ?? []
  const chainValid = detail.data?.event_chain_valid ?? null
  const outstanding = invoice !== undefined ? invoiceOutstanding(invoice) : 0

  if (detail.isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
    )
  }

  if (detail.isError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-2">
        <p className="text-sm font-medium">Could not load the invoice</p>
        <p className="text-xs text-muted-foreground">{apiErrorMessage(detail.error)}</p>
        <Button variant="outline" size="sm" onClick={() => void detail.refetch()}>
          <RefreshCw aria-hidden="true" />
          Try again
        </Button>
      </div>
    )
  }

  if (invoice === undefined) {
    return null
  }

  const creditNotes = credits.data ?? []

  return (
    <>
      <dl className="grid gap-3 sm:grid-cols-2">
        <SummaryItem label="Status">
          <StatusBadge status={invoice.status} />
        </SummaryItem>
        <SummaryItem label="Invoice number">
          <span className="font-mono">{invoice.invoice_number}</span>
        </SummaryItem>
        <SummaryItem label="Issue date">{formatDate(invoice.issue_date)}</SummaryItem>
        <SummaryItem label="Due date">{formatDate(invoice.due_date)}</SummaryItem>
        <SummaryItem label="Paid at">
          {invoice.paid_at !== null ? formatDateTime(invoice.paid_at) : 'Not paid yet'}
        </SummaryItem>
        <SummaryItem label="Latest credit note">
          {invoice.credit_note_number ?? 'None'}
        </SummaryItem>
      </dl>

      <section aria-labelledby="invoice-totals-heading" className="space-y-2">
        <h3 id="invoice-totals-heading" className="text-sm font-medium">
          Authoritative totals
        </h3>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <AmountCard
            label="Net"
            value={formatCurrency(invoice.total_net, invoice.currency)}
          />
          <AmountCard
            label="VAT"
            value={formatCurrency(invoice.total_vat, invoice.currency)}
          />
          <AmountCard
            label="Gross"
            value={formatCurrency(invoice.total_gross, invoice.currency)}
            emphasis
          />
          <AmountCard
            label="Paid"
            value={formatCurrency(invoice.paid_amount, invoice.currency)}
          />
          <AmountCard
            label="Credited"
            value={formatCurrency(invoice.credited_amount, invoice.currency)}
          />
          <AmountCard
            label="Outstanding"
            value={formatCurrency(outstanding.toFixed(2), invoice.currency)}
            emphasis
          />
        </dl>
      </section>

      <section aria-labelledby="invoice-vat-heading" className="space-y-2">
        <h3 id="invoice-vat-heading" className="text-sm font-medium">
          VAT breakdown
        </h3>
        {invoice.vat_breakdown.length === 0 ? (
          <p className="text-sm text-muted-foreground">No VAT rates are recorded.</p>
        ) : (
          <Table>
            <TableCaption className="sr-only">
              Invoice taxable base and VAT per rate
            </TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead>Rate</TableHead>
                <TableHead className="text-right">Taxable net</TableHead>
                <TableHead className="text-right">VAT</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoice.vat_breakdown.map((split) => (
                <TableRow key={split.rate}>
                  <TableCell>{formatPercent(split.rate)}</TableCell>
                  <TableCell className="text-right">
                    {formatCurrency(split.taxable_amount, invoice.currency)}
                  </TableCell>
                  <TableCell className="text-right">
                    {formatCurrency(split.tax_amount, invoice.currency)}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell className="font-medium">Total</TableCell>
                <TableCell className="text-right font-medium">
                  {formatCurrency(invoice.total_net, invoice.currency)}
                </TableCell>
                <TableCell className="text-right font-medium">
                  {formatCurrency(invoice.total_vat, invoice.currency)}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        )}
      </section>

      <section aria-labelledby="invoice-credit-notes-heading" className="space-y-2">
        <h3 id="invoice-credit-notes-heading" className="text-sm font-medium">
          Credit notes
        </h3>
        {credits.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : credits.isError ? (
          <div role="alert" className="space-y-2">
            <p className="text-sm text-destructive">{apiErrorMessage(credits.error)}</p>
            <Button variant="outline" size="sm" onClick={() => void credits.refetch()}>
              <RefreshCw aria-hidden="true" />
              Retry
            </Button>
          </div>
        ) : creditNotes.length === 0 ? (
          <p className="text-sm text-muted-foreground">No credit notes have been issued.</p>
        ) : (
          <ul className="space-y-2">
            {creditNotes.map((note) => (
              <li
                key={note.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background p-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-medium">
                      {note.credit_note_number}
                    </span>
                    <Badge variant="secondary">{titleCase(note.status)}</Badge>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Issued {formatDate(note.issue_date)}
                  </div>
                  {note.reason.length > 0 ? (
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {note.reason}
                    </p>
                  ) : null}
                </div>
                <span className="text-sm font-medium tabular-nums">
                  {formatCurrency(note.total_gross, invoice.currency)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="invoice-documents-heading" className="space-y-2">
        <h3 id="invoice-documents-heading" className="text-sm font-medium">
          E-invoice documents
        </h3>
        <p className="text-xs text-muted-foreground">
          Download the generated ZUGFeRD PDF/A-3 carrier or the XRechnung XML.
        </p>
        <InvoiceDocuments invoice={invoice} />
      </section>

      <EventTimeline
        events={events}
        chainValid={chainValid}
        currency={invoice.currency}
        description="Every invoice change appends a SHA-256 entry that commits to the previous hash."
      />

      {canIssueCredit ? (
        <SheetFooter className="px-0 pb-0">
          <div className="mr-auto text-xs text-muted-foreground">
            {outstanding > 0
              ? `Open amount ${formatCurrency(outstanding.toFixed(2), invoice.currency)}`
              : 'Invoice fully settled.'}
          </div>
          <Button
            type="button"
            disabled={outstanding <= 0}
            onClick={() => setCreditOpen(true)}
          >
            <RotateCcw aria-hidden="true" />
            Issue credit note
          </Button>
        </SheetFooter>
      ) : null}

      {creditOpen ? (
        <CreditNoteDialog invoice={invoice} onClose={() => setCreditOpen(false)} />
      ) : null}
    </>
  )
}

export function InvoiceDetailSheet({
  invoiceId,
  invoiceNumber,
  onOpenChange,
}: {
  invoiceId: string | null
  invoiceNumber: string | null
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={invoiceId !== null} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <ReceiptText className="h-3.5 w-3.5" aria-hidden="true" />
            Invoice detail
          </div>
          <SheetTitle>
            {invoiceNumber !== null ? `Invoice ${invoiceNumber}` : 'Invoice detail'}
          </SheetTitle>
          <SheetDescription>
            Authoritative totals, VAT breakdown, credit notes, documents and the tamper-evident
            GoBD event chain.
          </SheetDescription>
        </SheetHeader>
        <div className="flex-1 space-y-6 overflow-y-auto p-5">
          {invoiceId !== null ? <InvoiceDetailBody invoiceId={invoiceId} /> : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
