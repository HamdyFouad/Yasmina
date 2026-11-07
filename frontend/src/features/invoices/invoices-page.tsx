import { ReceiptText } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'

export function InvoicesPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Invoices & billing"
        description="Paginated invoices with GoBD status, totals and document downloads."
      />
      <EmptyState
        icon={ReceiptText}
        title="Invoice table is queued for the next phase"
        description="GET /invoices, GET /invoices/{id} and POST /invoices/{id}/payments are ready; this view will add filters, document downloads and the admin Mark-as-paid action."
        hint="Approval documents are served by GET /approvals/{offer_number}/documents/{xrechnung|zugferd}."
      />
    </div>
  )
}
