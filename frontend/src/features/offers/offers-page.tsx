import { Tags } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'

export function OffersPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Offers"
        description="Draft, pending-approval and approved customer offers with their deterministic totals."
      />
      <EmptyState
        icon={Tags}
        title="Offer workspace is queued for the next phase"
        description="GET /offers and GET /offers/{id} are already available. This view will add status filtering, line detail and approval state."
        hint="Four-eyes approval remains in POST /approvals/approve."
      />
    </div>
  )
}
