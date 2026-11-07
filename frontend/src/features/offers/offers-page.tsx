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
        title="Offer workspace is not built yet"
        description="Pricing is currently reviewed inside the tender detail sheet, which lists every customer offer linked to a tender with its line and VAT breakdown."
        hint="Open Tenders → select a tender → “Pricing & linked offers”."
      />
    </div>
  )
}
