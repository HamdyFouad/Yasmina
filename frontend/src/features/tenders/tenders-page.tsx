import { FileText } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'

export function TendersPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Tenders & pipeline"
        description="Discovered and published tenders, their deadlines, CPV codes and attached documents."
      />
      <EmptyState
        icon={FileText}
        title="Tender board is the next view to build"
        description="The table, status filters and the detail drawer will read from a new owner-scoped GET /tenders endpoint."
        hint="Existing building blocks: POST /tenders/upload, POST /tenders/{id}/match and POST /tenders/{id}/price."
      />
    </div>
  )
}
