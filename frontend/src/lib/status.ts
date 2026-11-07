/** Maps backend status enums to badge tones shared by tenders, offers and invoices. */
export type StatusTone = 'default' | 'secondary' | 'outline' | 'success' | 'warning' | 'destructive'

const SUCCESS = new Set(['PAID', 'APPROVED', 'ACCEPTED', 'AWARDED'])
const WARNING = new Set([
  'PENDING_APPROVAL',
  'PARTIALLY_PAID',
  'PARTIALLY_CREDITED',
  'IN_EVALUATION',
  'PUBLISHED',
])
const DESTRUCTIVE = new Set(['OVERDUE', 'REJECTED', 'CANCELLED', 'EXPIRED'])
const MUTED = new Set(['DRAFT', 'ARCHIVED', 'CLOSED', 'CREDITED'])

export function statusTone(status: string): StatusTone {
  const normalized = status.toUpperCase()
  if (SUCCESS.has(normalized)) {
    return 'success'
  }
  if (WARNING.has(normalized)) {
    return 'warning'
  }
  if (DESTRUCTIVE.has(normalized)) {
    return 'destructive'
  }
  if (MUTED.has(normalized)) {
    return 'secondary'
  }
  return 'default'
}
