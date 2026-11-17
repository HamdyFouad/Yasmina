import * as React from 'react'
import { AlertTriangle, Inbox, RotateCcw, Trash2 } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { apiErrorMessage } from '@/lib/api-error'
import { formatDateTime } from '@/lib/utils'
import type { IngestionJob } from '@/types/domain'

function shortError(value: string | null): string {
  if (value === null || value.length === 0) {
    return '—'
  }
  return value.length > 120 ? `${value.slice(0, 117)}…` : value
}

export function IngestionPanel({
  id,
  title,
  description,
  jobs,
  isLoading,
  isError,
  error,
  emptyTitle,
  emptyDescription,
  onRetry,
  onDiscard,
  actionsDisabled,
}: {
  id: string
  title: string
  description: string
  jobs: IngestionJob[]
  isLoading: boolean
  isError: boolean
  error: unknown
  emptyTitle: string
  emptyDescription: string
  onRetry?: (job: IngestionJob) => void
  onDiscard?: (job: IngestionJob) => void
  actionsDisabled: boolean
}) {
  const captionId = React.useId()
  const headingId = `${id}-heading`

  if (isLoading) {
    return (
      <section aria-labelledby={headingId} className="space-y-3">
        <h2 id={headingId} className="text-lg font-semibold tracking-tight">
          {title}
        </h2>
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      </section>
    )
  }

  if (isError) {
    return (
      <section aria-labelledby={headingId} className="space-y-3">
        <h2 id={headingId} className="text-lg font-semibold tracking-tight">
          {title}
        </h2>
        <Card>
          <CardContent className="flex items-start gap-3 p-5" role="alert">
            <AlertTriangle className="mt-0.5 h-5 w-5 text-destructive" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium">Could not load {title.toLowerCase()}</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(error)}</p>
            </div>
          </CardContent>
        </Card>
      </section>
    )
  }

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div>
        <h2 id={headingId} className="text-lg font-semibold tracking-tight">
          {title}
        </h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>

      {jobs.length === 0 ? (
        <EmptyState icon={Inbox} title={emptyTitle} description={emptyDescription} />
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            tabIndex={0}
            className="stacked-table max-h-[55vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <table className="w-full min-w-[820px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                {title} with tender, portal, status, retry budget, last error
                {onRetry !== undefined || onDiscard !== undefined ? ' and actions' : ''}
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  {['Tender', 'Portal', 'Status', 'Attempts', 'Updated', 'Last error'].map(
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
                  {onRetry !== undefined || onDiscard !== undefined ? (
                    <th
                      scope="col"
                      className="h-10 px-3 text-right text-xs font-medium uppercase tracking-wide text-muted-foreground"
                    >
                      Actions
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr
                    key={job.id}
                    className="border-b border-border transition-colors last:border-0 hover:bg-muted/50"
                  >
                    <th scope="row" className="max-w-64 px-3 py-3 text-left align-top">
                      <span className="block font-mono text-xs text-muted-foreground">
                        {job.tender_number ?? job.tender_id}
                      </span>
                      <span className="line-clamp-2 font-medium">
                        {job.tender_title ?? 'Unknown tender'}
                      </span>
                      {job.attachment_count > 0 ? (
                        <span className="mt-1 block text-[11px] font-normal text-muted-foreground">
                          {job.attachment_count.toLocaleString('de-DE')} attachment(s)
                        </span>
                      ) : null}
                    </th>
                    <td data-label="Portal" className="px-3 py-3 align-top">
                      {job.portal_name}
                    </td>
                    <td data-label="Status" className="px-3 py-3 align-top">
                      <StatusBadge status={job.status} />
                      {job.quarantined ? (
                        <span className="mt-1 block text-[11px] font-medium text-destructive">
                          Malware quarantine
                        </span>
                      ) : null}
                    </td>
                    <td
                      data-label="Attempts"
                      className="whitespace-nowrap px-3 py-3 align-top"
                    >
                      {job.attempts} / {job.max_attempts}
                    </td>
                    <td data-label="Updated" className="whitespace-nowrap px-3 py-3 align-top">
                      {job.updated_at !== null ? formatDateTime(job.updated_at) : '—'}
                    </td>
                    <td data-label="Last error" className="max-w-72 px-3 py-3 align-top text-xs">
                      <span className="line-clamp-3" title={job.last_error ?? undefined}>
                        {shortError(job.last_error)}
                      </span>
                    </td>
                    {onRetry !== undefined || onDiscard !== undefined ? (
                      <td
                        data-label="Actions"
                        className="whitespace-nowrap px-3 py-3 text-right align-top"
                      >
                        <div className="flex flex-wrap justify-end gap-1">
                          {onRetry !== undefined ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={actionsDisabled}
                              aria-label={`Retry ingestion job for ${job.tender_number ?? job.tender_id}`}
                              onClick={() => onRetry(job)}
                            >
                              <RotateCcw />
                              Retry
                            </Button>
                          ) : null}
                          {onDiscard !== undefined ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={actionsDisabled}
                              aria-label={`Discard ingestion job for ${job.tender_number ?? job.tender_id}`}
                              onClick={() => onDiscard(job)}
                            >
                              <Trash2 />
                              Discard
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </section>
  )
}
