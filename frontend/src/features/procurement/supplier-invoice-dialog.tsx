import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Loader2, ReceiptText } from 'lucide-react'
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
import { createSupplierInvoice } from '@/lib/procurement-api'
import { formatCurrency, formatDate, formatQuantity } from '@/lib/utils'
import type { PurchaseOrder, SupplierInvoice } from '@/types/domain'

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

function dateAfterDays(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

interface LineDraft {
  purchaseOrderLineId: string
  description: string
  unit: string
  quantity: string
  unitPrice: string
}

function initialLines(purchaseOrder: PurchaseOrder): LineDraft[] {
  return purchaseOrder.lines.map((line) => ({
    purchaseOrderLineId: line.id,
    description: line.description,
    unit: line.unit,
    quantity: line.quantity,
    unitPrice: line.unit_price_net,
  }))
}

function lineTotal(line: LineDraft): number {
  const quantity = Number.parseFloat(line.quantity)
  const price = Number.parseFloat(line.unitPrice)
  return Number.isFinite(quantity) && Number.isFinite(price) ? quantity * price : Number.NaN
}

export function SupplierInvoiceDialog({
  purchaseOrder,
  onClose,
  onCreated,
}: {
  purchaseOrder: PurchaseOrder
  onClose: () => void
  onCreated: (invoice: SupplierInvoice) => void
}) {
  const [invoiceNumber, setInvoiceNumber] = React.useState('')
  const [invoiceDate, setInvoiceDate] = React.useState(today())
  const [deliveryDate, setDeliveryDate] = React.useState(
    purchaseOrder.expected_delivery_date ?? today(),
  )
  const [dueDate, setDueDate] = React.useState(dateAfterDays(30))
  const [vatRate, setVatRate] = React.useState('0.1900')
  const [notes, setNotes] = React.useState('')
  const [lines, setLines] = React.useState<LineDraft[]>(() => initialLines(purchaseOrder))
  const [attempted, setAttempted] = React.useState(false)
  const invoiceNumberRef = React.useRef<HTMLInputElement>(null)

  const errors: Record<string, string> = {}
  if (invoiceNumber.trim().length === 0) {
    errors.invoiceNumber = 'Enter the supplier invoice number.'
  }
  if (!invoiceDate) errors.invoiceDate = 'Choose the invoice date.'
  if (dueDate && dueDate < invoiceDate) errors.dueDate = 'Due date must not precede the invoice date.'
  if (deliveryDate && deliveryDate < invoiceDate) {
    errors.deliveryDate = 'Delivery date must not precede the invoice date.'
  }
  lines.forEach((line, index) => {
    const quantity = Number.parseFloat(line.quantity)
    const price = Number.parseFloat(line.unitPrice)
    if (!Number.isFinite(quantity) || quantity <= 0) {
      errors[`quantity-${index}`] = 'Quantity must be greater than zero.'
    }
    if (!Number.isFinite(price) || price < 0) {
      errors[`price-${index}`] = 'Unit price must not be negative.'
    }
  })
  const computedTotal = lines.reduce((sum, line) => sum + lineTotal(line), 0)
  const totalNet = Number.isFinite(computedTotal) ? computedTotal.toFixed(2) : '0.00'

  const mutation = useMutation({
    mutationFn: () =>
      createSupplierInvoice({
        purchase_order_id: purchaseOrder.id,
        invoice_number: invoiceNumber.trim(),
        invoice_date: invoiceDate,
        delivery_date: deliveryDate || null,
        due_date: dueDate || null,
        total_net: totalNet,
        vat_rate: vatRate,
        notes: notes.trim() || null,
        lines: lines.map((line) => ({
          purchase_order_line_id: line.purchaseOrderLineId,
          quantity: line.quantity,
          unit_price_net: line.unitPrice,
        })),
      }),
    onSuccess: (invoice) => {
      if (invoice.status === 'EXCEPTION') {
        toast.warning('Supplier invoice needs review', {
          description: invoice.exception_codes.join(', '),
        })
      } else {
        toast.success('Supplier invoice matched', {
          description: `${invoice.invoice_number} · ${formatCurrency(invoice.total_net, invoice.currency)}`,
        })
      }
      onCreated(invoice)
      onClose()
    },
  })

  const updateLine = (index: number, patch: Partial<LineDraft>): void => {
    setLines((current) =>
      current.map((line, lineIndex) => (lineIndex === index ? { ...line, ...patch } : line)),
    )
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (mutation.isPending) return
    setAttempted(true)
    if (Object.keys(errors).length > 0) return
    mutation.mutate()
  }

  const error = (key: string): string | undefined => (attempted ? errors[key] : undefined)

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        className="max-h-[92vh] overflow-y-auto sm:max-w-4xl"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          invoiceNumberRef.current?.focus()
        }}
      >
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Record supplier invoice</DialogTitle>
            <DialogDescription>
              Match {purchaseOrder.po_number} from {purchaseOrder.supplier_name}. Quantity, unit
              price, total and delivery deviations are queued for review.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="supplier-invoice-number">Invoice number</Label>
              <Input
                id="supplier-invoice-number"
                ref={invoiceNumberRef}
                value={invoiceNumber}
                maxLength={128}
                onChange={(event) => setInvoiceNumber(event.target.value)}
                aria-invalid={error('invoiceNumber') ? true : undefined}
              />
              {error('invoiceNumber') ? (
                <p role="alert" className="text-xs text-destructive">
                  {error('invoiceNumber')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-invoice-date">Invoice date</Label>
              <Input
                id="supplier-invoice-date"
                type="date"
                value={invoiceDate}
                onChange={(event) => setInvoiceDate(event.target.value)}
                aria-invalid={error('invoiceDate') ? true : undefined}
              />
              {error('invoiceDate') ? (
                <p role="alert" className="text-xs text-destructive">
                  {error('invoiceDate')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-invoice-delivery">Delivery date</Label>
              <Input
                id="supplier-invoice-delivery"
                type="date"
                value={deliveryDate}
                onChange={(event) => setDeliveryDate(event.target.value)}
                aria-invalid={error('deliveryDate') ? true : undefined}
              />
              {error('deliveryDate') ? (
                <p role="alert" className="text-xs text-destructive">
                  {error('deliveryDate')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-invoice-due">Due date</Label>
              <Input
                id="supplier-invoice-due"
                type="date"
                value={dueDate}
                onChange={(event) => setDueDate(event.target.value)}
                aria-invalid={error('dueDate') ? true : undefined}
              />
              {error('dueDate') ? (
                <p role="alert" className="text-xs text-destructive">
                  {error('dueDate')}
                </p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="supplier-invoice-vat">VAT rate</Label>
              <Input
                id="supplier-invoice-vat"
                value={vatRate}
                onChange={(event) => setVatRate(event.target.value)}
              />
            </div>
            <div className="rounded-md border border-border bg-muted/20 p-3">
              <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Invoice total net
              </p>
              <p className="text-left text-lg font-semibold">
                {formatCurrency(totalNet, purchaseOrder.currency)}
              </p>
              <p className="text-xs text-muted-foreground">
                PO total {formatCurrency(purchaseOrder.total_net, purchaseOrder.currency)}
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium">Invoice lines</p>
            <p className="text-xs text-muted-foreground">
              Quantities and unit prices start from the PO. Edit them to record a supplier
              deviation; the matcher will queue the exception.
            </p>
            <div className="space-y-2">
              {lines.map((line, index) => (
                <div
                  key={line.purchaseOrderLineId}
                  className="grid gap-3 rounded-md border border-border p-3 sm:grid-cols-[1fr_8rem_8rem]"
                >
                  <div>
                    <p className="text-sm font-medium">{line.description}</p>
                    <p className="text-xs text-muted-foreground">
                      Expected {formatQuantity(purchaseOrder.lines[index]?.quantity ?? '0')}{' '}
                      {line.unit} ·{' '}
                      {formatCurrency(
                        purchaseOrder.lines[index]?.unit_price_net ?? '0',
                        purchaseOrder.currency,
                      )}
                    </p>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`supplier-invoice-quantity-${index}`}>Quantity</Label>
                    <Input
                      id={`supplier-invoice-quantity-${index}`}
                      inputMode="decimal"
                      value={line.quantity}
                      onChange={(event) => updateLine(index, { quantity: event.target.value })}
                      aria-invalid={error(`quantity-${index}`) ? true : undefined}
                    />
                    {error(`quantity-${index}`) ? (
                      <p role="alert" className="text-xs text-destructive">
                        {error(`quantity-${index}`)}
                      </p>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`supplier-invoice-price-${index}`}>Unit price net</Label>
                    <Input
                      id={`supplier-invoice-price-${index}`}
                      inputMode="decimal"
                      value={line.unitPrice}
                      onChange={(event) => updateLine(index, { unitPrice: event.target.value })}
                      aria-invalid={error(`price-${index}`) ? true : undefined}
                    />
                    {error(`price-${index}`) ? (
                      <p role="alert" className="text-xs text-destructive">
                        {error(`price-${index}`)}
                      </p>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="supplier-invoice-notes">Notes</Label>
              <Textarea
                id="supplier-invoice-notes"
                rows={3}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </div>
            <div className="rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">Delivery check</p>
              <p>
                PO expects delivery by {formatDate(purchaseOrder.expected_delivery_date)}. A later
                supplier delivery date raises a delivery exception.
              </p>
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
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? <Loader2 className="animate-spin" /> : <ReceiptText />}
              Record and match invoice
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
