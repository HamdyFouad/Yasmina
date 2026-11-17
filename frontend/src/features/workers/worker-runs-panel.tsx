import * as React from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { apiErrorMessage } from '@/lib/api-error'
import { formatDateTime } from '@/lib/utils'
import type { WorkerRun } from '@/types/domain'

function durationLabel(run: WorkerRun): string {
  if (run.duration_ms === null) {
    return run.status === 'running' ? 'In flight' : '—'
  }
  if (run.duration_ms < 1000) {
    return `${run.duration_ms} ms`
  }
  return `${(run.duration_ms / 1000).toFixed(1)} s`
}

function resourceLabel(run: WorkerRun): string {
  return run.run_type === 'scrape' ? `Portal: ${run.resource}` : run.resource
}

export function WorkerRunsPanel({
  runs,
  isLoading,
  isError,
  error,
  isFetching,
  onRefresh,
}: {
  runs: WorkerRun[]
  isLoading: boolean
  isError: boolean
  error: unknown
  isFetching: boolean
  onRefresh: () => void
}) {
  const captionId = React.useId()

  return (
    <section className="space-y-3" aria-labelledby="worker-runs-heading">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="worker-runs-heading" className="text-lg font-semibold tracking-tight">
            Worker runs
          </h2>
          <p className="text-sm text-muted-foreground">
            Recent portal scrapes and ingestion batches with duration, item counts and failures.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={isFetching}
          onClick={onRefresh}
        >
          <RefreshCw className={isFetching ? 'animate-spin' : undefined} aria-hidden="true" />
          Refresh runs
        </Button>
      </div>

      {isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : isError ? (
        <Card>
          <CardContent className="flex items-start gap-3 p-5" role="alert">
            <AlertTriangle className="mt-0.5 h-5 w-5 text-destructive" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium">Could not load worker history</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(error)}</p>
              <Button type="button" variant="outline" size="sm" className="mt-2" onClick={onRefresh}>
                <RefreshCw />
                Try again
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : runs.length === 0 ? (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            No worker runs have been recorded yet. The daily scheduler and ingestion worker append
            their history here.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            aria-busy={isFetching}
            tabIndex={0}
            className="stacked-table max-h-[55vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <table className="w-full min-w-[860px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                Worker runs with type, resource, status, duration and error counts
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  {['Started', 'Type', 'Resource', 'Status', 'Duration', 'Items', 'Errors'].map(
                    (label) => (
                      <th
                        key={label}
                        scope="col"
                        className={`h-10 px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground ${
                          label === 'Items' || label === 'Errors' ? 'text-right' : 'text-left'
                        }`}
                      >
                        {label}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    className="border-b border-border transition-colors last:border-0 hover:bg-muted/50"
                  >
                    <th scope="row" className="whitespace-nowrap px-3 py-3 text-left align-top">
                      <time dateTime={run.started_at}>{formatDateTime(run.started_at)}</time>
                      <span className="block text-[11px] text-muted-foreground">
                        {run.worker_id}
                      </span>
                    </th>
                    <td data-label="Type" className="px-3 py-3 align-top capitalize">
                      {run.run_type}
                    </td>
                    <td data-label="Resource" className="max-w-64 px-3 py-3 align-top">
                      <span className="line-clamp-2">{resourceLabel(run)}</span>
                    </td>
                    <td data-label="Status" className="px-3 py-3 align-top">
                      <StatusBadge status={run.status} />
                    </td>
                    <td
                      data-label="Duration"
                      className="whitespace-nowrap px-3 py-3 align-top"
                    >
                      {durationLabel(run)}
                    </td>
                    <td
                      data-label="Items"
                      className="whitespace-nowrap px-3 py-3 text-right align-top"
                    >
                      {run.items_processed.toLocaleString('de-DE')}
                    </td>
                    <td
                      data-label="Errors"
                      className="whitespace-nowrap px-3 py-3 text-right align-top"
                    >
                      <span className={run.errors_count > 0 ? 'font-medium text-destructive' : ''}>
                        {run.errors_count.toLocaleString('de-DE')}
                      </span>
                      {run.error !== null ? (
                        <span
                          className="mt-1 block max-w-64 truncate text-[11px] font-normal text-muted-foreground"
                          title={run.error}
                        >
                          {run.error}
                        </span>
                      ) : null}
                    </td>
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
