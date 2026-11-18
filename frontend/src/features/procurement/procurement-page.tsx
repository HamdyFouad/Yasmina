import * as React from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  PackageCheck,
  Plus,
  ReceiptText,
  RefreshCw,
  Search,
  Send,
  ShoppingCart,
  Truck,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { TablePagination } from '@/components/table-pagination'
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
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/auth-context'
import { CreatePurchaseOrderDialog } from '@/features/procurement/create-purchase-order-dialog'
import { SupplierInvoiceDialog } from '@/features/procurement/supplier-invoice-dialog'
import { apiErrorMessage } from '@/lib/api-error'
import {
  fetchMarginSummaries,
  fetchProcurementHealth,
  fetchPurchaseOrders,
  fetchSupplierInvoices,
  rematchSupplierInvoice,
  transitionPurchaseOrder,
} from '@/lib/procurement-api'
import { formatCurrency, formatDate, formatPercent } from '@/lib/utils'
import type {
  PurchaseOrder,
  PurchaseOrderStatus,
  SupplierInvoiceStatus,
} from '@/types/domain'

const PAGE_SIZE = 20

const ORDER_FILTERS: Array<{ value: PurchaseOrderStatus | 'ALL'; label: string }> = [
  { value: 'ALL', label: 'All PO states' },
  { value: 'DRAFT', label: 'Draft' },
  { value: 'SENT', label: 'Sent' },
  { value: 'CONFIRMED', label: 'Confirmed' },
  { value: 'DELIVERED', label: 'Delivered' },
  { value: 'CANCELLED', label: 'Cancelled' },
]

const INVOICE_FILTERS: Array<{ value: SupplierInvoiceStatus | 'ALL'; label: string }> = [
  { value: 'ALL', label: 'All invoice states' },
  { value: 'EXCEPTION', label: 'Exceptions' },
  { value: 'MATCHED', label: 'Matched' },
  { value: 'PENDING', label: 'Pending' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'REJECTED', label: 'Rejected' },
]

function MetricCard({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: string | number
  hint: string
  tone?: 'default' | 'warning' | 'destructive' | 'success'
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
        <p
          className={`mt-1 text-2xl font-semibold tracking-tight ${
            tone === 'destructive'
              ? 'text-destructive'
              : tone === 'warning'
                ? 'text-warning'
                : tone === 'success'
                  ? 'text-success'
                  : ''
          }`}
        >
          {value}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  )
}

function orderActions(order: PurchaseOrder): Array<{
  status: PurchaseOrderStatus
  label: string
  icon: typeof Send
  destructive?: boolean
}> {
  if (order.status === 'DRAFT') {
    return [
      { status: 'SENT', label: 'Send', icon: Send },
      { status: 'CANCELLED', label: 'Cancel', icon: XCircle, destructive: true },
    ]
  }
  if (order.status === 'SENT') {
    return [
      { status: 'CONFIRMED', label: 'Confirm', icon: CheckCircle2 },
      { status: 'CANCELLED', label: 'Cancel', icon: XCircle, destructive: true },
    ]
  }
  if (order.status === 'CONFIRMED') {
    return [
      { status: 'DELIVERED', label: 'Mark delivered', icon: PackageCheck },
      { status: 'CANCELLED', label: 'Cancel', icon: XCircle, destructive: true },
    ]
  }
  return []
}

function receivedPercent(order: PurchaseOrder): number {
  const ordered = order.lines.reduce((sum, line) => sum + Number(line.quantity), 0)
  const received = order.lines.reduce((sum, line) => sum + Number(line.received_quantity), 0)
  if (!Number.isFinite(ordered) || ordered <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((received / ordered) * 100)))
}

export function ProcurementPage() {
  const queryClient = useQueryClient()
  const { hasRole } = useAuth()
  const canWrite = hasRole('admin', 'sales')
  const [orderPage, setOrderPage] = React.useState(0)
  const [orderStatus, setOrderStatus] = React.useState<PurchaseOrderStatus | 'ALL'>('ALL')
  const [orderSearch, setOrderSearch] = React.useState('')
  const deferredSearch = React.useDeferredValue(orderSearch)
  const [invoicePage, setInvoicePage] = React.useState(0)
  const [invoiceStatus, setInvoiceStatus] = React.useState<SupplierInvoiceStatus | 'ALL'>('ALL')
  const [createOpen, setCreateOpen] = React.useState(false)
  const [invoiceFor, setInvoiceFor] = React.useState<PurchaseOrder | null>(null)
  const poCaptionId = React.useId()
  const invoiceCaptionId = React.useId()
  const marginCaptionId = React.useId()

  const health = useQuery({
    queryKey: ['procurement', 'health'],
    queryFn: fetchProcurementHealth,
    refetchInterval: 60_000,
  })
  const orders = useQuery({
    queryKey: ['procurement', 'purchase-orders', { orderPage, orderStatus, deferredSearch }],
    queryFn: () =>
      fetchPurchaseOrders({
        ...(orderStatus !== 'ALL' ? { status: orderStatus } : {}),
        search: deferredSearch.trim(),
        limit: PAGE_SIZE,
        offset: orderPage * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  const invoices = useQuery({
    queryKey: ['procurement', 'supplier-invoices', { invoicePage, invoiceStatus }],
    queryFn: () =>
      fetchSupplierInvoices({
        ...(invoiceStatus !== 'ALL' ? { status: invoiceStatus } : {}),
        limit: PAGE_SIZE,
        offset: invoicePage * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  const exceptions = useQuery({
    queryKey: ['procurement', 'supplier-invoices', 'exceptions'],
    queryFn: () => fetchSupplierInvoices({ exceptionOnly: true, limit: 10 }),
    refetchInterval: 60_000,
  })
  const margins = useQuery({
    queryKey: ['procurement', 'margins'],
    queryFn: () => fetchMarginSummaries({ limit: 50 }),
    refetchInterval: 60_000,
  })

  const invalidateProcurement = (): void => {
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['procurement', 'health'] }),
      queryClient.invalidateQueries({ queryKey: ['procurement', 'purchase-orders'] }),
      queryClient.invalidateQueries({ queryKey: ['procurement', 'supplier-invoices'] }),
      queryClient.invalidateQueries({ queryKey: ['procurement', 'margins'] }),
    ])
  }

  const transition = useMutation({
    mutationFn: ({ id, status }: { id: string; status: PurchaseOrderStatus }) =>
      transitionPurchaseOrder(id, status),
    onSuccess: (purchaseOrder) => {
      toast.success(`Purchase order ${purchaseOrder.po_number} updated`, {
        description: `Status: ${purchaseOrder.status}`,
      })
      invalidateProcurement()
    },
    onError: (error) => {
      toast.error(apiErrorMessage(error))
    },
  })

  const rematch = useMutation({
    mutationFn: (invoiceId: string) => rematchSupplierInvoice(invoiceId),
    onSuccess: (invoice) => {
      toast.success(`Supplier invoice ${invoice.invoice_number} re-matched`, {
        description:
          invoice.status === 'EXCEPTION'
            ? invoice.exception_codes.join(', ')
            : 'No deviations found.',
      })
      invalidateProcurement()
    },
    onError: (error) => {
      toast.error(apiErrorMessage(error))
    },
  })

  const orderRows = orders.data?.purchase_orders ?? []
  const invoiceRows = invoices.data?.supplier_invoices ?? []
  const exceptionRows = exceptions.data?.supplier_invoices ?? []
  const marginRows = margins.data?.margins ?? []

  const handleCreated = (_purchaseOrder: PurchaseOrder): void => {
    setOrderPage(0)
    setOrderStatus('ALL')
    invalidateProcurement()
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Procurement"
        description="Purchase orders, supplier invoice matching and actual-margin control."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                health.refetch()
                orders.refetch()
                invoices.refetch()
                exceptions.refetch()
                margins.refetch()
              }}
            >
              <RefreshCw />
              Refresh
            </Button>
            {canWrite ? (
              <Button type="button" size="sm" onClick={() => setCreateOpen(true)}>
                <Plus />
                Create PO
              </Button>
            ) : null}
          </div>
        }
      />

      <section aria-labelledby="procurement-health-heading" className="space-y-3">
        <div>
          <h2 id="procurement-health-heading" className="text-lg font-semibold tracking-tight">
            Procurement health
          </h2>
          <p className="text-sm text-muted-foreground">
            Live purchase-order counts and supplier invoices that need operator review.
          </p>
        </div>
        <div className="procurement-metric-container">
          <div className="procurement-metric-grid">
          <MetricCard
            label="Purchase orders"
            value={health.data?.purchase_orders ?? 0}
            hint="Draft through delivered procurement commitments."
          />
          <MetricCard
            label="Open exceptions"
            value={health.data?.exceptions ?? 0}
            hint="Invoice quantity, price, total or delivery deviations."
            tone={health.data?.exceptions ? 'destructive' : 'success'}
          />
          <MetricCard
            label="Tracked offers"
            value={marginRows.length}
            hint="Offers with at least one purchase order for margin comparison."
          />
          <MetricCard
            label="Awaiting delivery"
            value={orderRows.filter((order) => order.status === 'CONFIRMED').length}
            hint="Confirmed orders on this page that are not delivered yet."
            tone="warning"
          />
          </div>
        </div>
      </section>

      <section aria-labelledby="procurement-exceptions-heading" className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2
              id="procurement-exceptions-heading"
              className="text-lg font-semibold tracking-tight"
            >
              Exception queue
            </h2>
            <p className="text-sm text-muted-foreground">
              Supplier invoices whose line quantities, prices, totals or delivery dates deviate
              from the PO.
            </p>
          </div>
          <Badge variant={exceptionRows.length > 0 ? 'destructive' : 'secondary'}>
            {exceptionRows.length} open
          </Badge>
        </div>

        {exceptions.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : exceptionRows.length === 0 ? (
          <Card>
            <CardContent className="flex items-start gap-3 p-4 text-sm text-muted-foreground">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
              <p>No supplier-invoice exceptions are waiting for review.</p>
            </CardContent>
          </Card>
        ) : (
          <div className="procurement-exception-container">
            <div className="procurement-exception-list">
              {exceptionRows.map((invoice) => (
              <Card key={invoice.id} className="border-destructive/30 bg-destructive/[0.03]">
                <CardContent className="space-y-3 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-medium">{invoice.invoice_number}</p>
                      <p className="text-xs text-muted-foreground">
                        {invoice.supplier_name} · {invoice.po_number}
                      </p>
                    </div>
                    <StatusBadge status={invoice.status} />
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {invoice.exception_codes.map((code) => (
                      <Badge key={code} variant="destructive" className="font-mono text-[10px]">
                        {code}
                      </Badge>
                    ))}
                  </div>
                  <dl className="grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <dt className="text-muted-foreground">Invoice net</dt>
                      <dd className="font-medium">
                        {formatCurrency(invoice.total_net, invoice.currency)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Variance</dt>
                      <dd className="font-medium text-destructive">
                        {formatCurrency(invoice.variance_net, invoice.currency)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Delivered</dt>
                      <dd>{formatDate(invoice.delivery_date)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Invoice date</dt>
                      <dd>{formatDate(invoice.invoice_date)}</dd>
                    </div>
                  </dl>
                  {canWrite ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={rematch.isPending}
                      onClick={() => rematch.mutate(invoice.id)}
                    >
                      <RefreshCw />
                      Re-match
                    </Button>
                  ) : null}
                </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}
      </section>

      <section aria-labelledby="purchase-orders-heading" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="purchase-orders-heading" className="text-lg font-semibold tracking-tight">
              Purchase orders
            </h2>
            <p className="text-sm text-muted-foreground">
              Track the PO lifecycle, delivery progress and supplier invoicing.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="po-search">Search</Label>
              <div className="relative">
                <Search
                  className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"
                  aria-hidden="true"
                />
                <Input
                  id="po-search"
                  value={orderSearch}
                  onChange={(event) => {
                    setOrderSearch(event.target.value)
                    setOrderPage(0)
                  }}
                  placeholder="PO number or supplier"
                  className="w-64 pl-8"
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="po-status">Status</Label>
              <Select
                value={orderStatus}
                onValueChange={(value) => {
                  setOrderStatus(value as PurchaseOrderStatus | 'ALL')
                  setOrderPage(0)
                }}
              >
                <SelectTrigger id="po-status" className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ORDER_FILTERS.map((filter) => (
                    <SelectItem key={filter.value} value={filter.value}>
                      {filter.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        {orders.isLoading ? (
          <Card>
            <CardContent className="space-y-3 p-5">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-10 w-full" />
              ))}
            </CardContent>
          </Card>
        ) : orders.isError ? (
          <Card>
            <CardContent role="alert" className="p-5 text-sm text-destructive">
              {apiErrorMessage(orders.error)}
            </CardContent>
          </Card>
        ) : orderRows.length === 0 ? (
          <EmptyState
            icon={ShoppingCart}
            title="No purchase orders found"
            description="Accept a supplier quote, then create a PO from this workspace."
          />
        ) : (
          <Card>
            <div
              role="region"
              aria-labelledby={poCaptionId}
              aria-busy={orders.isFetching}
              tabIndex={0}
              className="stacked-table max-h-[62vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              <table className="w-full min-w-[980px] border-collapse text-sm">
                <caption id={poCaptionId} className="sr-only">
                  Purchase orders with supplier, lifecycle status, total, expected delivery,
                  receipt progress and actions
                </caption>
                <thead className="sticky top-0 z-10 bg-card shadow-sm">
                  <tr className="border-b border-border">
                    {['Purchase order', 'Status', 'Total net', 'Expected delivery', 'Received', 'Actions'].map(
                      (label) => (
                        <th
                          key={label}
                          scope="col"
                          className="h-10 px-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                        >
                          {label}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {orderRows.map((order) => (
                    <tr
                      key={order.id}
                      className="border-b border-border last:border-0 hover:bg-muted/50"
                    >
                      <th scope="row" className="max-w-72 px-3 py-3 text-left align-top">
                        <span className="block font-medium">{order.po_number}</span>
                        <span className="block text-xs text-muted-foreground">
                          {order.supplier_name}
                        </span>
                        <span className="block text-[11px] text-muted-foreground">
                          {order.lines.length} line(s) · ordered {formatDate(order.order_date)}
                        </span>
                      </th>
                      <td data-label="Status" className="px-3 py-3 align-top">
                        <StatusBadge status={order.status} />
                      </td>
                      <td
                        data-label="Total net"
                        className="whitespace-nowrap px-3 py-3 align-top font-medium"
                      >
                        {formatCurrency(order.total_net, order.currency)}
                      </td>
                      <td data-label="Expected delivery" className="whitespace-nowrap px-3 py-3 align-top">
                        {formatDate(order.expected_delivery_date)}
                        {order.delivered_at !== null ? (
                          <span className="block text-xs text-success">
                            delivered {formatDate(order.delivered_at)}
                          </span>
                        ) : null}
                      </td>
                      <td data-label="Received" className="px-3 py-3 align-top">
                        <span className="flex items-center gap-2">
                          <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                            <span
                              className="block h-full rounded-full bg-success"
                              style={{ width: `${receivedPercent(order)}%` }}
                            />
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {receivedPercent(order)}%
                          </span>
                        </span>
                      </td>
                      <td data-label="Actions" className="whitespace-nowrap px-3 py-3 align-top">
                        <div className="flex flex-wrap justify-end gap-1">
                          {canWrite
                            ? orderActions(order).map((action) => (
                                <Button
                                  key={action.status}
                                  type="button"
                                  variant={action.destructive ? 'ghost' : 'outline'}
                                  size="sm"
                                  disabled={transition.isPending}
                                  aria-label={`${action.label} purchase order ${order.po_number}`}
                                  onClick={() =>
                                    transition.mutate({ id: order.id, status: action.status })
                                  }
                                >
                                  <action.icon />
                                  {action.label}
                                </Button>
                              ))
                            : null}
                          {canWrite && order.status !== 'CANCELLED' ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              aria-label={`Record supplier invoice for ${order.po_number}`}
                              onClick={() => setInvoiceFor(order)}
                            >
                              <ReceiptText />
                              Invoice
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <TablePagination
          label="purchase orders"
          total={orders.data?.count ?? 0}
          page={orderPage}
          pageSize={PAGE_SIZE}
          shown={orderRows.length}
          isFetching={orders.isFetching}
          onPageChange={setOrderPage}
        />
      </section>

      <section aria-labelledby="supplier-invoices-heading" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="supplier-invoices-heading" className="text-lg font-semibold tracking-tight">
              Supplier invoices
            </h2>
            <p className="text-sm text-muted-foreground">
              Incoming billing matched against PO quantities, unit prices, totals and delivery.
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="invoice-status">Match status</Label>
            <Select
              value={invoiceStatus}
              onValueChange={(value) => {
                setInvoiceStatus(value as SupplierInvoiceStatus | 'ALL')
                setInvoicePage(0)
              }}
            >
              <SelectTrigger id="invoice-status" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {INVOICE_FILTERS.map((filter) => (
                  <SelectItem key={filter.value} value={filter.value}>
                    {filter.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {invoices.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : invoices.isError ? (
          <Card>
            <CardContent role="alert" className="p-5 text-sm text-destructive">
              {apiErrorMessage(invoices.error)}
            </CardContent>
          </Card>
        ) : invoiceRows.length === 0 ? (
          <EmptyState
            icon={ReceiptText}
            title="No supplier invoices yet"
            description="Record an invoice against a PO to run the deterministic matching engine."
          />
        ) : (
          <Card>
            <div
              role="region"
              aria-labelledby={invoiceCaptionId}
              aria-busy={invoices.isFetching}
              tabIndex={0}
              className="stacked-table max-h-[52vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              <table className="w-full min-w-[980px] border-collapse text-sm">
                <caption id={invoiceCaptionId} className="sr-only">
                  Supplier invoices with purchase order, status, net total, variance, delivery and
                  matching actions
                </caption>
                <thead className="sticky top-0 z-10 bg-card shadow-sm">
                  <tr className="border-b border-border">
                    {['Invoice', 'PO', 'Status', 'Total net', 'Variance', 'Delivery', 'Exceptions', 'Actions'].map(
                      (label) => (
                        <th
                          key={label}
                          scope="col"
                          className="h-10 px-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                        >
                          {label}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {invoiceRows.map((invoice) => (
                    <tr
                      key={invoice.id}
                      className={`border-b border-border last:border-0 ${
                        invoice.status === 'EXCEPTION'
                          ? 'bg-destructive/[0.04] hover:bg-destructive/[0.07]'
                          : 'hover:bg-muted/50'
                      }`}
                    >
                      <th scope="row" className="max-w-64 px-3 py-3 text-left align-top">
                        <span className="block font-medium">{invoice.invoice_number}</span>
                        <span className="block text-xs text-muted-foreground">
                          {invoice.supplier_name}
                        </span>
                      </th>
                      <td data-label="PO" className="whitespace-nowrap px-3 py-3 align-top font-mono text-xs">
                        {invoice.po_number}
                      </td>
                      <td data-label="Status" className="px-3 py-3 align-top">
                        <StatusBadge status={invoice.status} />
                      </td>
                      <td data-label="Total net" className="whitespace-nowrap px-3 py-3 align-top font-medium">
                        {formatCurrency(invoice.total_net, invoice.currency)}
                      </td>
                      <td data-label="Variance" className="whitespace-nowrap px-3 py-3 align-top text-xs">
                        <span
                          className={
                            Number(invoice.variance_net) === 0 ? '' : 'font-medium text-destructive'
                          }
                        >
                          {formatCurrency(invoice.variance_net, invoice.currency)}
                        </span>
                      </td>
                      <td data-label="Delivery" className="whitespace-nowrap px-3 py-3 align-top">
                        {formatDate(invoice.delivery_date)}
                      </td>
                      <td data-label="Exceptions" className="max-w-64 px-3 py-3 align-top">
                        {invoice.exception_codes.length === 0 ? (
                          <span className="text-xs text-muted-foreground">—</span>
                        ) : (
                          <span className="flex flex-wrap gap-1">
                            {invoice.exception_codes.map((code) => (
                              <Badge
                                key={code}
                                variant="destructive"
                                className="font-mono text-[10px]"
                              >
                                {code}
                              </Badge>
                            ))}
                          </span>
                        )}
                      </td>
                      <td data-label="Actions" className="whitespace-nowrap px-3 py-3 text-right align-top">
                        {canWrite ? (
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={rematch.isPending}
                            aria-label={`Re-match supplier invoice ${invoice.invoice_number}`}
                            onClick={() => rematch.mutate(invoice.id)}
                          >
                            <RefreshCw />
                            Re-match
                          </Button>
                        ) : (
                          <span className="text-xs text-muted-foreground">Read only</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <TablePagination
          label="supplier invoices"
          total={invoices.data?.count ?? 0}
          page={invoicePage}
          pageSize={PAGE_SIZE}
          shown={invoiceRows.length}
          isFetching={invoices.isFetching}
          onPageChange={setInvoicePage}
        />
      </section>

      <section aria-labelledby="margin-heading" className="space-y-3">
        <div>
          <h2 id="margin-heading" className="text-lg font-semibold tracking-tight">
            Actual vs quoted margin
          </h2>
          <p className="text-sm text-muted-foreground">
            Compares the approved offer margin with supplier invoices booked against its POs.
            Rejected invoices are excluded.
          </p>
        </div>

        {margins.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : margins.isError ? (
          <Card>
            <CardContent role="alert" className="p-5 text-sm text-destructive">
              {apiErrorMessage(margins.error)}
            </CardContent>
          </Card>
        ) : marginRows.length === 0 ? (
          <Card>
            <CardContent className="flex items-start gap-3 p-4 text-sm text-muted-foreground">
              <Truck className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
              <p>Margin tracking starts when a PO is linked to an approved customer offer.</p>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <div
              role="region"
              aria-labelledby={marginCaptionId}
              aria-busy={margins.isFetching}
              tabIndex={0}
              className="stacked-table max-h-[48vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              <table className="w-full min-w-[900px] border-collapse text-sm">
                <caption id={marginCaptionId} className="sr-only">
                  Offer margin comparison with quoted and actual margin rates, variance, coverage
                  and exceptions
                </caption>
                <thead className="sticky top-0 z-10 bg-card shadow-sm">
                  <tr className="border-b border-border">
                    {['Offer', 'Quoted margin', 'Actual margin', 'Variance', 'Coverage', 'Exceptions'].map(
                      (label) => (
                        <th
                          key={label}
                          scope="col"
                          className="h-10 px-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                        >
                          {label}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {marginRows.map((margin) => (
                    <tr key={margin.offer_id} className="border-b border-border last:border-0 hover:bg-muted/50">
                      <th scope="row" className="max-w-64 px-3 py-3 text-left align-top">
                        <span className="block font-medium">{margin.offer_number}</span>
                        <span className="block text-xs text-muted-foreground">
                          {margin.customer_name}
                        </span>
                      </th>
                      <td data-label="Quoted margin" className="px-3 py-3 align-top">
                        <span className="font-medium">
                          {formatPercent(margin.quoted_margin_rate ?? 0)}
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          {formatCurrency(margin.quoted_margin_net, margin.currency)}
                        </span>
                      </td>
                      <td data-label="Actual margin" className="px-3 py-3 align-top">
                        {margin.actual_margin_rate === null ? (
                          <span className="text-xs text-muted-foreground">Awaiting invoice</span>
                        ) : (
                          <>
                            <span className="font-medium">
                              {formatPercent(margin.actual_margin_rate)}
                            </span>
                            <span className="block text-xs text-muted-foreground">
                              cost{' '}
                              {formatCurrency(margin.actual_cost_net, margin.currency)}
                            </span>
                          </>
                        )}
                      </td>
                      <td data-label="Variance" className="px-3 py-3 align-top">
                        {margin.margin_variance_rate === null ? (
                          <span className="text-xs text-muted-foreground">—</span>
                        ) : (
                          <span
                            className={
                              Number(margin.margin_variance_rate) < 0
                                ? 'font-medium text-destructive'
                                : 'font-medium text-success'
                            }
                          >
                            {formatPercent(margin.margin_variance_rate)}
                          </span>
                        )}
                      </td>
                      <td data-label="Coverage" className="px-3 py-3 align-top">
                        {margin.coverage_rate === null ? (
                          <span className="text-xs text-muted-foreground">—</span>
                        ) : (
                          formatPercent(margin.coverage_rate)
                        )}
                      </td>
                      <td data-label="Exceptions" className="px-3 py-3 align-top">
                        <Badge variant={margin.exception_count > 0 ? 'destructive' : 'secondary'}>
                          {margin.exception_count}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </section>

      {createOpen ? (
        <CreatePurchaseOrderDialog
          onClose={() => setCreateOpen(false)}
          onCreated={handleCreated}
        />
      ) : null}
      {invoiceFor !== null ? (
        <SupplierInvoiceDialog
          purchaseOrder={invoiceFor}
          onClose={() => setInvoiceFor(null)}
          onCreated={() => {
            setInvoiceFor(null)
            invalidateProcurement()
          }}
        />
      ) : null}
    </div>
  )
}
