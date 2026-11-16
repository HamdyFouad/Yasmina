import type { Invoice } from '@/types/domain'

/** Open amount derived from the invoice's authoritative gross, paid and credited totals. */
export function invoiceOutstanding(invoice: Invoice): number {
  const gross = Number.parseFloat(invoice.total_gross)
  if (Number.isNaN(gross)) {
    return 0
  }
  const paid = Number.parseFloat(invoice.paid_amount)
  const credited = Number.parseFloat(invoice.credited_amount)
  const remaining = gross - (Number.isNaN(paid) ? 0 : paid) - (Number.isNaN(credited) ? 0 : credited)
  return remaining > 0 ? Math.round(remaining * 100) / 100 : 0
}

/**
 * Parse a money input with either a dot or a German comma. Returns `null` when
 * the value is not a positive amount with at most two decimal places.
 */
export function parseMoneyInput(value: string): number | null {
  const normalized = value.trim().replace(',', '.')
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) {
    return null
  }
  const amount = Number.parseFloat(normalized)
  if (!Number.isFinite(amount) || amount <= 0) {
    return null
  }
  return Math.round(amount * 100) / 100
}
