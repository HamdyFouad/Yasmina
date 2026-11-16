import * as React from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Inbox,
  Loader2,
  RefreshCw,
  RotateCcw,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import { fetchOutboxJobs, retryOutboxJob } from '@/lib/ops-api'
import { formatDateTime } from '@/lib/utils'
import type { QuotationSyncJob, QuotationSyncStatus } from '@/types/domain'

const PAGE_SIZE = 25

const STATUS_OPTIONS: { value: QuotationSyncStatus | 'ALL'; label: string }[] = [
  { value: 'ALL', label: 'All statuses' },
  { value: 'failed', label: 'Failed' },
  { value: 'pending', label: 'Pending' },
  { value: 'processing', label: 'Processing' },
  { value: 'succeeded', label: 'Succeeded' },
  { value: 'skipped', label: 'Skipped' },
]

function isQuotationSyncStatus(value: string): value is QuotationSyncStatus {
  return STATUS_OPTIONS.some((option) => option.value === value && option.value !== 'ALL')
}

function nextAttempt(job: QuotationSyncJob): React.ReactNode {
  if (job.status === 'pending') {
    return (
      <time dateTime={job.available_at} className="whitespace-nowrap">
        {formatDateTime(job.available_at)}
      </time>
    )
  }
  if (job.status === 'processing') {
    return <span className="text-muted-foreground">In flight</span>
  }
  if (job.status === 'failed') {
    return <span className="text-destructive">Stopped after retries</span>
  }
  return <span className="text-muted-foreground">—</span>
}

export function OutboxPage() {
  const { hasRole } = useAuth()
  const queryClient = useQueryClient()
  const captionId = React.useId()

  const [status, setStatus] = React.useState<QuotationSyncStatus | null>(null)
  const [page, setPage] = React.useState(0)
  const [announcement, setAnnouncement] = React.useState('')

  const canRetry = hasRole('admin', 'approver')
  const offset = page * PAGE_SIZE

  const jobsQuery = useQuery({
    queryKey: ['ops', 'outbox', { status, offset }],
    queryFn: () => fetchOutboxJobs({ status, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })

  const retry = useMutation({
    mutationFn: (job: QuotationSyncJob) => retryOutboxJob(job.id),
    onSuccess: async (_job, submitted) => {
      setAnnouncement(
        `Retry queued for ${submitted.offer_number}. The job is pending again with zero attempts.`,
      )
      toast.success('Retry queued', { description: submitted.offer_number })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ops', 'outbox'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'outbox-status'] }),
      ])
    },
    onError: (error, submitted) => {
      setAnnouncement(`Retry failed for ${submitted.offer_number}: ${apiErrorMessage(error)}`)
      toast.error('Retry failed', { description: apiErrorMessage(error) })
    },
  })

  const jobs = jobsQuery.data?.jobs ?? []
  const total = jobsQuery.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = offset + jobs.length

  const handleStatusChange = (value: string): void => {
    setStatus(isQuotationSyncStatus(value) ? value : null)
    setPage(0)
  }

  const handleRetry = (job: QuotationSyncJob): void => {
    if (!canRetry || retry.isPending) {
      return
    }
    retry.mutate(job)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="ERPNext outbox"
        description="Quotation sync jobs, their retry budget and the last ERPNext error. Failed jobs can be requeued by an admin or approver."
        actions={
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={jobsQuery.isFetching}
            onClick={() => void jobsQuery.refetch()}
          >
            {jobsQuery.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Refresh
          </Button>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="outbox-status" className="text-sm text-muted-foreground">
            Status
          </label>
          <Select value={status ?? 'ALL'} onValueChange={handleStatusChange}>
            <SelectTrigger id="outbox-status" className="w-48">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <p className="text-xs text-muted-foreground">
          {total.toLocaleString('de-DE')} quotation job(s)
        </p>
      </div>

      {/* One polite live region per page: announcements never interrupt. */}
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {jobsQuery.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : jobsQuery.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <AlertTriangle className="h-6 w-6 text-destructive" aria-hidden="true" />
            <div role="alert">
              <p className="text-sm font-medium">Could not load the outbox</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(jobsQuery.error)}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void jobsQuery.refetch()}>
              <RefreshCw />
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No quotation jobs found"
          description={
            status === null
              ? 'Approving a customer offer enqueues an ERPNext quotation job; the sync worker drains this queue.'
              : 'No jobs match the selected status filter.'
          }
          hint="Every job records its attempts, retry budget and last ERPNext error."
        />
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            aria-busy={jobsQuery.isFetching}
            tabIndex={0}
            className="stacked-table max-h-[70vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <table className="w-full min-w-[880px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                ERPNext quotation sync jobs with status, attempts and the last error
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Offer
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Status
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Attempts
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Next attempt
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Last error
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-right align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const retrying = retry.isPending && retry.variables?.id === job.id
                  return (
                    <tr
                      key={job.id}
                      className="border-b border-border transition-colors last:border-0 hover:bg-muted/50"
                    >
                      <th
                        scope="row"
                        className="px-3 py-3 text-left align-top font-mono text-sm font-medium"
                      >
                        <Link
                          to={`/offers?offer=${encodeURIComponent(job.offer_id)}`}
                          aria-label={`Open offer ${job.offer_number}`}
                          className="rounded-sm text-primary underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                        >
                          {job.offer_number}
                        </Link>
                        {job.quotation_name !== null ? (
                          <span className="block font-sans text-xs font-normal text-muted-foreground">
                            {job.quotation_name}
                          </span>
                        ) : null}
                      </th>
                      <td data-label="Status" className="px-3 py-3 align-top">
                        <StatusBadge status={job.status} />
                      </td>
                      <td
                        data-label="Attempts"
                        className="whitespace-nowrap px-3 py-3 align-top tabular-nums"
                      >
                        {job.attempts}/{job.max_attempts}
                      </td>
                      <td data-label="Next attempt" className="px-3 py-3 align-top text-xs">
                        {nextAttempt(job)}
                      </td>
                      <td
                        data-label="Last error"
                        className="max-w-[46ch] px-3 py-3 align-top text-xs text-muted-foreground"
                      >
                        {job.last_error !== null && job.last_error.length > 0 ? (
                          <span className="line-clamp-3 break-words">{job.last_error}</span>
                        ) : (
                          <span aria-hidden="true">—</span>
                        )}
                      </td>
                      <td
                        data-label="Actions"
                        className="px-3 py-3 text-right align-top"
                      >
                        {job.status === 'failed' ? (
                          canRetry ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              aria-disabled={retrying}
                              aria-busy={retrying}
                              aria-label={`Retry quotation ${job.offer_number}`}
                              onClick={() => handleRetry(job)}
                            >
                              {retrying ? (
                                <Loader2 className="animate-spin" aria-hidden="true" />
                              ) : (
                                <RotateCcw aria-hidden="true" />
                              )}
                              {retrying ? 'Retrying' : 'Retry'}
                            </Button>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              Admin or approver only
                            </span>
                          )
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {total === 0 ? 'No results' : `Showing ${rangeStart}–${rangeEnd} of ${total}`}
        </p>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page === 0 || jobsQuery.isFetching}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
          >
            <ChevronLeft aria-hidden="true" />
            Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {page + 1} of {pageCount}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page + 1 >= pageCount || jobsQuery.isFetching}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      </div>
    </div>
  )
}
