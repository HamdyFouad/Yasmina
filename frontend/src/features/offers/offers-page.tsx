import * as React from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  RefreshCw,
  Tags,
} from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
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
import { ApprovalDialog } from '@/features/offers/approval-dialog'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import { unitLabel } from '@/lib/labels'
import { downloadX84Bid, fetchOffer, fetchOffers } from '@/lib/offers-api'
import { formatCurrency, formatDate, formatDateTime, formatPercent } from '@/lib/utils'
import type { CustomerOffer, CustomerOfferStatus, UnitOfMeasure } from '@/types/domain'

const PAGE_SIZE = 25

const STATUS_OPTIONS: { value: CustomerOfferStatus | 'ALL'; label: string }[] = [
  { value: 'ALL', label: 'All statuses' },
  { value: 'DRAFT', label: 'Draft' },
  { value: 'PENDING_APPROVAL', label: 'Pending approval' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'REJECTED', label: 'Rejected' },
  { value: 'SENT', label: 'Sent' },
  { value: 'ACCEPTED', label: 'Accepted' },
  { value: 'EXPIRED', label: 'Expired' },
]

const APPROVABLE_STATUSES: ReadonlySet<CustomerOfferStatus> = new Set([
  'DRAFT',
  'PENDING_APPROVAL',
])

function isCustomerOfferStatus(value: string): value is CustomerOfferStatus {
  return STATUS_OPTIONS.some((option) => option.value === value && option.value !== 'ALL')
}

function SummaryItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  )
}

const X84_EXPORT_STATUSES: ReadonlySet<CustomerOfferStatus> = new Set([
  'APPROVED',
  'SENT',
  'ACCEPTED',
])

function OfferDetailSheet({ offerId, onClose }: { offerId: string; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ['offer', offerId],
    queryFn: () => fetchOffer(offerId),
  })
  const [downloading, setDownloading] = React.useState(false)
  const data = detail.data

  const handleX84Download = async (): Promise<void> => {
    if (data === undefined) {
      return
    }
    setDownloading(true)
    try {
      await downloadX84Bid(data.offer.id, `${data.offer.offer_number}.x84`)
      toast.success('GAEB .x84 bid downloaded')
    } catch (error) {
      toast.error('GAEB export failed', { description: apiErrorMessage(error) })
    } finally {
      setDownloading(false)
    }
  }

  return (
    <Sheet open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>
            {data !== undefined ? `Offer ${data.offer.offer_number}` : 'Offer details'}
          </SheetTitle>
          <SheetDescription>
            Signed approval state, VAT breakdown and line items.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          {detail.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }, (_, index) => (
                <Skeleton key={index} className="h-9 w-full" />
              ))}
            </div>
          ) : detail.isError ? (
            <div role="alert" className="flex flex-col items-start gap-2">
              <p className="text-sm font-medium">Could not load the offer</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(detail.error)}</p>
              <Button variant="outline" size="sm" onClick={() => void detail.refetch()}>
                <RefreshCw aria-hidden="true" />
                Try again
              </Button>
            </div>
          ) : data !== undefined ? (
            <>
              <dl className="grid gap-3 sm:grid-cols-2">
                <SummaryItem label="Customer">{data.offer.customer_name}</SummaryItem>
                <SummaryItem label="Status">
                  <StatusBadge status={data.offer.status} />
                </SummaryItem>
                <SummaryItem label="Owner">{data.offer.owner ?? '—'}</SummaryItem>
                <SummaryItem label="Created">{formatDate(data.offer.created_at)}</SummaryItem>
                <SummaryItem label="Valid until">
                  {formatDate(data.offer.valid_until)}
                </SummaryItem>
                <SummaryItem label="Margin">
                  {formatPercent(data.offer.margin_rate)}
                </SummaryItem>
                <SummaryItem label="Approved by">
                  {data.offer.approved_by ?? 'Not signed yet'}
                </SummaryItem>
                <SummaryItem label="Approved at">
                  {data.offer.approved_at !== null
                    ? formatDateTime(data.offer.approved_at)
                    : '—'}
                </SummaryItem>
                <SummaryItem label="ERPNext quotation">
                  {data.offer.erpnext_quotation_name ?? 'Not queued'}
                </SummaryItem>
                <SummaryItem label="Signature">
                  {data.offer.ed25519_approval_signature !== null ? (
                    <span className="break-all font-mono text-xs">
                      {data.offer.ed25519_approval_signature.slice(0, 24)}…
                    </span>
                  ) : (
                    'Unsigned'
                  )}
                </SummaryItem>
              </dl>

              <section className="space-y-2" aria-labelledby="offer-vat-heading">
                <h3 id="offer-vat-heading" className="text-sm font-medium">
                  VAT breakdown
                </h3>
                <Table>
                  <TableCaption className="sr-only">
                    Server-computed taxable base and VAT per rate
                  </TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Rate</TableHead>
                      <TableHead className="text-right">Taxable net</TableHead>
                      <TableHead className="text-right">VAT</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.vat_breakdown.map((split) => (
                      <TableRow key={split.rate}>
                        <TableCell>{formatPercent(split.rate)}</TableCell>
                        <TableCell className="text-right">
                          {formatCurrency(split.taxable_amount, data.offer.currency)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatCurrency(split.tax_amount, data.offer.currency)}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow>
                      <TableCell className="font-medium">Total</TableCell>
                      <TableCell className="text-right font-medium">
                        {formatCurrency(data.offer.total_net, data.offer.currency)}
                      </TableCell>
                      <TableCell className="text-right font-medium">
                        {formatCurrency(data.offer.total_vat, data.offer.currency)}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </section>

              <section className="space-y-2" aria-labelledby="offer-lines-heading">
                <h3 id="offer-lines-heading" className="text-sm font-medium">
                  Line items
                </h3>
                <Table>
                  <TableCaption className="sr-only">
                    Offer positions with quantities, prices and VAT rates
                  </TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Pos.</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead className="text-right">Qty</TableHead>
                      <TableHead className="text-right">Unit net</TableHead>
                      <TableHead className="text-right">Total net</TableHead>
                      <TableHead className="text-right">VAT</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.offer.lines.map((line) => (
                      <TableRow key={line.id}>
                        <TableCell className="font-mono text-xs">{line.position_number}</TableCell>
                        <TableCell className="max-w-72">
                          <span className="line-clamp-2">{line.description}</span>
                          {line.position_number === 'FREIGHT' ||
                          line.position_number === 'PALLET' ? (
                            <Badge variant="secondary" className="mt-1">
                              Surcharge
                            </Badge>
                          ) : null}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-right">
                          {formatQuantityLabel(line.quantity, line.unit)}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-right">
                          {formatCurrency(line.unit_price_net, data.offer.currency)}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-right">
                          {formatCurrency(line.total_net, data.offer.currency)}
                        </TableCell>
                        <TableCell className="text-right">{formatPercent(line.vat_rate)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>

              <dl className="grid gap-2 border-t border-border pt-3 text-sm sm:grid-cols-3">
                <SummaryItem label="Net total">
                  {formatCurrency(data.offer.total_net, data.offer.currency)}
                </SummaryItem>
                <SummaryItem label="VAT total">
                  {formatCurrency(data.offer.total_vat, data.offer.currency)}
                </SummaryItem>
                <SummaryItem label="Gross total">
                  {formatCurrency(data.offer.total_gross, data.offer.currency)}
                </SummaryItem>
              </dl>

              {X84_EXPORT_STATUSES.has(data.offer.status) ? (
                <div className="flex justify-end border-t border-border pt-3">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={downloading}
                    onClick={() => void handleX84Download()}
                  >
                    {downloading ? <Loader2 className="animate-spin" /> : <Download />}
                    Download .x84 bid
                  </Button>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function formatQuantityLabel(quantity: string, unit: UnitOfMeasure): string {
  return `${quantity} ${unitLabel(unit)}`
}

export function OffersPage() {
  const { user, hasRole } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const captionId = React.useId()

  const [status, setStatus] = React.useState<CustomerOfferStatus | null>(null)
  const [ownerInput, setOwnerInput] = React.useState('')
  const [owner, setOwner] = React.useState('')
  const [page, setPage] = React.useState(0)
  const [approving, setApproving] = React.useState<CustomerOffer | null>(null)

  const canApprove = hasRole('approver', 'admin')
  const canViewAll = hasRole('approver', 'admin', 'viewer')
  const selectedOfferId = searchParams.get('offer')
  const offset = page * PAGE_SIZE

  const offersQuery = useQuery({
    queryKey: ['offers', { status, owner, offset }],
    queryFn: () =>
      fetchOffers({
        limit: PAGE_SIZE,
        offset,
        ...(status !== null ? { status } : {}),
        ...(owner.length > 0 ? { owner } : {}),
      }),
    placeholderData: keepPreviousData,
  })

  const offers = offersQuery.data?.offers ?? []
  const total = offersQuery.data?.count ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = offset + offers.length

  const openDetails = (offerId: string): void => {
    const next = new URLSearchParams(searchParams)
    next.set('offer', offerId)
    setSearchParams(next)
  }

  const closeDetails = (): void => {
    const next = new URLSearchParams(searchParams)
    next.delete('offer')
    setSearchParams(next, { replace: true })
  }

  const handleStatusChange = (value: string): void => {
    setStatus(isCustomerOfferStatus(value) ? value : null)
    setPage(0)
  }

  const handleOwnerSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setOwner(ownerInput.trim())
    setPage(0)
  }

  const clearOwner = (): void => {
    setOwnerInput('')
    setOwner('')
    setPage(0)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Offers"
        description="Draft, pending-approval and approved customer offers with their deterministic totals. Approvers sign server-side with the custody key; four-eyes blocks owner self-approval."
        actions={
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={offersQuery.isFetching}
            onClick={() => void offersQuery.refetch()}
          >
            {offersQuery.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Refresh
          </Button>
        }
      />

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="offer-status" className="text-sm text-muted-foreground">
              Status
            </Label>
            <Select value={status ?? 'ALL'} onValueChange={handleStatusChange}>
              <SelectTrigger id="offer-status" className="w-48">
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

          {canViewAll ? (
            <form className="flex items-end gap-2" onSubmit={handleOwnerSubmit}>
              <div className="space-y-1">
                <Label htmlFor="offer-owner" className="text-sm text-muted-foreground">
                  Owner
                </Label>
                <Input
                  id="offer-owner"
                  value={ownerInput}
                  onChange={(event) => setOwnerInput(event.target.value)}
                  placeholder="e.g. sales-a"
                  className="w-44"
                />
              </div>
              <Button type="submit" variant="outline" size="sm">
                Apply
              </Button>
              {owner.length > 0 ? (
                <Button type="button" variant="ghost" size="sm" onClick={clearOwner}>
                  Clear
                </Button>
              ) : null}
            </form>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          {total.toLocaleString('de-DE')} offer(s)
        </p>
      </div>

      {offersQuery.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : offersQuery.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <AlertTriangle className="h-6 w-6 text-destructive" aria-hidden="true" />
            <div role="alert">
              <p className="text-sm font-medium">Could not load offers</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(offersQuery.error)}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void offersQuery.refetch()}>
              <RefreshCw aria-hidden="true" />
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : offers.length === 0 ? (
        <EmptyState
          icon={Tags}
          title="No offers found"
          description={
            status === null && owner.length === 0
              ? 'Offers are created from a tender workflow. Price a tender to produce the first offer.'
              : 'No offers match the current filters.'
          }
          hint="Approving an offer signs its canonical payload and queues an ERPNext quotation."
        />
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            aria-busy={offersQuery.isFetching}
            tabIndex={0}
            className="stacked-table max-h-[70vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <table className="w-full min-w-[920px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                Customer offers with owner, status, totals and approval actions
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Offer
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Customer
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Status
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Owner
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-right align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Gross total
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-right align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {offers.map((offer) => {
                  const ownedByViewer = offer.owner !== null && offer.owner === user?.subject
                  return (
                    <tr
                      key={offer.id}
                      className="border-b border-border transition-colors last:border-0 hover:bg-muted/50"
                    >
                      <th
                        scope="row"
                        className="px-3 py-3 text-left align-top font-mono text-sm font-medium"
                      >
                        {offer.offer_number}
                        <span className="block font-sans text-xs font-normal text-muted-foreground">
                          {offer.lines.length} line(s)
                        </span>
                      </th>
                      <td data-label="Customer" className="px-3 py-3 align-top">
                        {offer.customer_name}
                        {offer.customer_reference !== null ? (
                          <span className="block text-xs text-muted-foreground">
                            {offer.customer_reference}
                          </span>
                        ) : null}
                      </td>
                      <td data-label="Status" className="px-3 py-3 align-top">
                        <StatusBadge status={offer.status} />
                      </td>
                      <td data-label="Owner" className="px-3 py-3 align-top text-sm">
                        {offer.owner ?? '—'}
                        {ownedByViewer ? (
                          <span className="block text-xs text-muted-foreground">(you)</span>
                        ) : null}
                      </td>
                      <td
                        data-label="Gross total"
                        className="whitespace-nowrap px-3 py-3 text-right align-top font-medium"
                      >
                        {formatCurrency(offer.total_gross, offer.currency)}
                      </td>
                      <td data-label="Actions" className="px-3 py-3 align-top">
                        <div className="flex flex-wrap items-center justify-end gap-1">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            aria-label={`View details for ${offer.offer_number}`}
                            onClick={() => openDetails(offer.id)}
                          >
                            Details
                          </Button>
                          {canApprove && APPROVABLE_STATUSES.has(offer.status) ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              aria-label={`Approve offer ${offer.offer_number}`}
                              onClick={() => setApproving(offer)}
                            >
                              <CheckCircle2 aria-hidden="true" />
                              Approve
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
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
            disabled={page === 0 || offersQuery.isFetching}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
          >
            <ChevronLeft aria-hidden="true" />
            Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {page + 1} of {pageCount}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page + 1 >= pageCount || offersQuery.isFetching}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      </div>

      {selectedOfferId !== null ? (
        <OfferDetailSheet key={selectedOfferId} offerId={selectedOfferId} onClose={closeDetails} />
      ) : null}

      {approving !== null ? (
        <ApprovalDialog
          key={approving.id}
          offer={approving}
          onClose={() => setApproving(null)}
        />
      ) : null}
    </div>
  )
}
