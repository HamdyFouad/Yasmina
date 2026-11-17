import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, AlertTriangle, Loader2, Play, RefreshCw, ShieldAlert } from 'lucide-react'

import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { IngestionActionDialog } from '@/features/workers/ingestion-action-dialog'
import { IngestionPanel } from '@/features/workers/ingestion-panel'
import { ProbePortalDialog } from '@/features/workers/probe-portal-dialog'
import { WorkerRunsPanel } from '@/features/workers/worker-runs-panel'
import { apiErrorMessage } from '@/lib/api-error'
import { fetchIngestionJobs, fetchPortalTargets, fetchWorkerRuns } from '@/lib/ops-api'
import { formatDateTime } from '@/lib/utils'
import type { IngestionJob } from '@/types/domain'

function MetricCard({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: string | number
  hint: string
  tone?: 'default' | 'warning' | 'destructive'
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
        <p
          className={`mt-1 text-2xl font-semibold tracking-tight ${
            tone === 'destructive'
              ? 'text-destructive'
              : tone === 'warning'
                ? 'text-warning'
                : ''
          }`}
        >
          {value}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  )
}

export function WorkersPage() {
  const [probeOpen, setProbeOpen] = React.useState(false)
  const [actionState, setActionState] = React.useState<{
    job: IngestionJob
    action: 'retry' | 'discard'
  } | null>(null)

  const runs = useQuery({
    queryKey: ['ops', 'worker-runs'],
    queryFn: () => fetchWorkerRuns({ limit: 50 }),
    refetchInterval: 60_000,
  })
  const ingestion = useQuery({
    queryKey: ['ops', 'ingestion'],
    queryFn: () => fetchIngestionJobs({ limit: 200 }),
    refetchInterval: 60_000,
  })
  const portals = useQuery({
    queryKey: ['ops', 'portals'],
    queryFn: fetchPortalTargets,
    staleTime: 300_000,
  })

  const jobs = ingestion.data?.jobs ?? []
  const queue = jobs.filter((job) => job.status === 'pending' || job.status === 'processing')
  const quarantined = jobs.filter((job) => job.quarantined)
  const deadLetter = jobs.filter((job) => job.status === 'failed' && !job.quarantined)

  const refreshAll = (): void => {
    void Promise.all([runs.refetch(), ingestion.refetch(), portals.refetch()])
  }

  const isFetching = runs.isFetching || ingestion.isFetching
  const latestRun = runs.data?.runs[0] ?? null

  return (
    <div className="space-y-8">
      <PageHeader
        title="Workers & Ingestion"
        description="Worker run history, portal diagnostics, the ingestion queue and terminal quarantine/DLQ jobs."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={isFetching}
              onClick={refreshAll}
            >
              {isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Refresh
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={portals.isLoading || portals.data === undefined}
              onClick={() => setProbeOpen(true)}
            >
              <Play />
              Run probe
            </Button>
          </div>
        }
      />

      <section aria-labelledby="ingestion-health-heading" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="ingestion-health-heading" className="text-lg font-semibold tracking-tight">
              Queue health
            </h2>
            <p className="text-sm text-muted-foreground">
              Live counts from the ingestion outbox and the most recent worker execution.
            </p>
          </div>
          {latestRun !== null ? (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Activity className="h-3.5 w-3.5" aria-hidden="true" />
              Last run {formatDateTime(latestRun.started_at)}
              <StatusBadge status={latestRun.status} />
            </p>
          ) : null}
        </div>

        {ingestion.isError ? (
          <p
            role="alert"
            className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
          >
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {apiErrorMessage(ingestion.error)}
          </p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="Queued / processing"
            value={(ingestion.data?.status_counts.pending ?? 0) + (ingestion.data?.status_counts.processing ?? 0)}
            hint="Claimed by the ingestion worker or waiting for its next pass."
            tone="warning"
          />
          <MetricCard
            label="Quarantined"
            value={ingestion.data?.quarantine_count ?? 0}
            hint="Malware-positive jobs that never reached a parser."
            tone="destructive"
          />
          <MetricCard
            label="Dead-letter"
            value={Math.max(
              0,
              (ingestion.data?.status_counts.failed ?? 0) - (ingestion.data?.quarantine_count ?? 0),
            )}
            hint="Terminal failures that exhausted the retry budget."
            tone="destructive"
          />
          <MetricCard
            label="Completed"
            value={ingestion.data?.status_counts.completed ?? 0}
            hint="Successfully parsed and persisted ingestion jobs."
          />
        </div>
      </section>

      <WorkerRunsPanel
        runs={runs.data?.runs ?? []}
        isLoading={runs.isLoading}
        isError={runs.isError}
        error={runs.error}
        isFetching={runs.isFetching}
        onRefresh={() => void runs.refetch()}
      />

      <IngestionPanel
        id="ingestion-queue"
        title="Ingestion queue"
        description="Jobs waiting for or currently held by the ingestion worker."
        jobs={queue}
        isLoading={ingestion.isLoading}
        isError={ingestion.isError}
        error={ingestion.error}
        emptyTitle="The ingestion queue is empty"
        emptyDescription="Scraped notices are queued here before ClamAV scanning, parsing and persistence."
        actionsDisabled={actionState !== null}
      />

      <IngestionPanel
        id="quarantined-tenders"
        title="Quarantined tenders"
        description="Malware-positive ingestions stopped before Docling or pyGAEB. Retrying downloads and rescans the attachment."
        jobs={quarantined}
        isLoading={ingestion.isLoading}
        isError={ingestion.isError}
        error={ingestion.error}
        emptyTitle="No quarantined tenders"
        emptyDescription="ClamAV has not rejected any queued attachment."
        actionsDisabled={actionState !== null}
        onRetry={(job) => setActionState({ job, action: 'retry' })}
        onDiscard={(job) => setActionState({ job, action: 'discard' })}
      />

      <IngestionPanel
        id="ingestion-dlq"
        title="Dead-letter queue"
        description="Terminal parsing or processing failures. Retry after fixing the source or upstream service; discard to close them out."
        jobs={deadLetter}
        isLoading={ingestion.isLoading}
        isError={ingestion.isError}
        error={ingestion.error}
        emptyTitle="No dead-letter jobs"
        emptyDescription="Every failed ingestion still has retry budget or was discarded."
        actionsDisabled={actionState !== null}
        onRetry={(job) => setActionState({ job, action: 'retry' })}
        onDiscard={(job) => setActionState({ job, action: 'discard' })}
      />

      {ingestion.isError ? (
        <p className="sr-only" role="alert">
          Ingestion tables unavailable: {apiErrorMessage(ingestion.error)}
        </p>
      ) : null}

      {probeOpen ? (
        <ProbePortalDialog
          portals={portals.data?.portals ?? []}
          onClose={() => setProbeOpen(false)}
        />
      ) : null}

      {actionState !== null ? (
        <IngestionActionDialog
          key={`${actionState.job.id}-${actionState.action}`}
          job={actionState.job}
          action={actionState.action}
          onClose={() => setActionState(null)}
          onCompleted={() => {
            setActionState(null)
            void ingestion.refetch()
          }}
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="secondary">Read-only probe</Badge>
        <span>
          Portal probes never write to PostgreSQL, MinIO or the ingestion outbox.
        </span>
        {portals.isError ? (
          <span className="flex items-center gap-1 text-destructive">
            <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
            Portal catalogue unavailable: {apiErrorMessage(portals.error)}
          </span>
        ) : null}
      </div>
    </div>
  )
}
