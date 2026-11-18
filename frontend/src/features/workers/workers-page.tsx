import * as React from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  Loader2,
  Play,
  RefreshCw,
  ShieldAlert,
  Unlock,
} from 'lucide-react'

import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { IngestionActionDialog } from '@/features/workers/ingestion-action-dialog'
import { IngestionPanel } from '@/features/workers/ingestion-panel'
import {
  ManualWorkerDialog,
  type WorkerActionMode,
} from '@/features/workers/manual-worker-dialog'
import { ProbePortalDialog } from '@/features/workers/probe-portal-dialog'
import { WorkerRunDetailSheet } from '@/features/workers/worker-run-detail-sheet'
import { WorkerRunsPanel } from '@/features/workers/worker-runs-panel'
import { apiErrorMessage } from '@/lib/api-error'
import { fetchIngestionJobs, fetchPortalTargets, fetchWorkerRuns } from '@/lib/ops-api'
import { formatDateTime } from '@/lib/utils'
import type {
  IngestionJob,
  WorkerRun,
  WorkerRunStatus,
  WorkerRunType,
} from '@/types/domain'

const RUN_PAGE_SIZE = 20
const JOB_PAGE_SIZE = 20

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
  const [manualMode, setManualMode] = React.useState<WorkerActionMode | null>(null)
  const [detailRun, setDetailRun] = React.useState<WorkerRun | null>(null)
  const [actionState, setActionState] = React.useState<{
    job: IngestionJob
    action: 'retry' | 'discard'
  } | null>(null)

  const [runsPage, setRunsPage] = React.useState(0)
  const [runType, setRunType] = React.useState<WorkerRunType | null>(null)
  const [runStatus, setRunStatus] = React.useState<WorkerRunStatus | null>(null)
  const [queueStatus, setQueueStatus] = React.useState<'pending' | 'processing' | null>(null)
  const [queuePage, setQueuePage] = React.useState(0)
  const [quarantinePage, setQuarantinePage] = React.useState(0)
  const [dlqPage, setDlqPage] = React.useState(0)

  const runs = useQuery({
    queryKey: ['ops', 'worker-runs', { runsPage, runType, runStatus }],
    queryFn: () =>
      fetchWorkerRuns({
        limit: RUN_PAGE_SIZE,
        offset: runsPage * RUN_PAGE_SIZE,
        ...(runType !== null ? { runType } : {}),
        ...(runStatus !== null ? { status: runStatus } : {}),
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  const queue = useQuery({
    queryKey: ['ops', 'ingestion', 'queue', { queuePage, queueStatus }],
    queryFn: () =>
      fetchIngestionJobs({
        queue: true,
        ...(queueStatus !== null ? { status: queueStatus } : {}),
        limit: JOB_PAGE_SIZE,
        offset: queuePage * JOB_PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  const quarantine = useQuery({
    queryKey: ['ops', 'ingestion', 'quarantine', { quarantinePage }],
    queryFn: () =>
      fetchIngestionJobs({
        quarantined: true,
        limit: JOB_PAGE_SIZE,
        offset: quarantinePage * JOB_PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  const deadLetter = useQuery({
    queryKey: ['ops', 'ingestion', 'dlq', { dlqPage }],
    queryFn: () =>
      fetchIngestionJobs({
        status: 'failed',
        quarantined: false,
        limit: JOB_PAGE_SIZE,
        offset: dlqPage * JOB_PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  const portals = useQuery({
    queryKey: ['ops', 'portals'],
    queryFn: fetchPortalTargets,
    staleTime: 300_000,
  })

  const summary = queue.data
  const latestRun = runs.data?.runs[0] ?? null
  const deadLetterCount = Math.max(
    0,
    (summary?.status_counts.failed ?? 0) - (summary?.quarantine_count ?? 0),
  )

  const refreshAll = (): void => {
    void Promise.all([
      runs.refetch(),
      queue.refetch(),
      quarantine.refetch(),
      deadLetter.refetch(),
      portals.refetch(),
    ])
  }

  const refreshIngestion = (): void => {
    void Promise.all([queue.refetch(), quarantine.refetch(), deadLetter.refetch(), runs.refetch()])
  }

  const isFetching =
    runs.isFetching ||
    queue.isFetching ||
    quarantine.isFetching ||
    deadLetter.isFetching

  const handleRunTypeChange = (value: WorkerRunType | null): void => {
    setRunType(value)
    setRunsPage(0)
  }

  const handleRunStatusChange = (value: WorkerRunStatus | null): void => {
    setRunStatus(value)
    setRunsPage(0)
  }

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
              <ShieldAlert />
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

        {queue.isError ? (
          <p
            role="alert"
            className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
          >
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {apiErrorMessage(queue.error)}
          </p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="Queued / processing"
            value={(summary?.status_counts.pending ?? 0) + (summary?.status_counts.processing ?? 0)}
            hint="Claimed by the ingestion worker or waiting for its next pass."
            tone="warning"
          />
          <MetricCard
            label="Quarantined"
            value={summary?.quarantine_count ?? 0}
            hint="Malware-positive jobs that never reached a parser."
            tone="destructive"
          />
          <MetricCard
            label="Dead-letter"
            value={deadLetterCount}
            hint="Terminal failures that exhausted the retry budget."
            tone="destructive"
          />
          <MetricCard
            label="Completed"
            value={summary?.status_counts.completed ?? 0}
            hint="Successfully parsed and persisted ingestion jobs."
          />
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/20 p-3">
        <span className="mr-1 text-sm font-medium">Manual worker actions</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={portals.isLoading || portals.data === undefined}
          onClick={() => setManualMode('scrape')}
        >
          <Play />
          Run scrape
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setManualMode('ingestion')}
        >
          <Play />
          Process queue
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setManualMode('release')}
        >
          <Unlock />
          Release locks
        </Button>
        <span className="text-xs text-muted-foreground">
          Starts a detached one-shot worker; refresh history to follow progress.
        </span>
      </div>

      <WorkerRunsPanel
        runs={runs.data?.runs ?? []}
        total={runs.data?.count ?? 0}
        page={runsPage}
        pageSize={RUN_PAGE_SIZE}
        isLoading={runs.isLoading}
        isError={runs.isError}
        error={runs.error}
        isFetching={runs.isFetching}
        runType={runType}
        runStatus={runStatus}
        onRunTypeChange={handleRunTypeChange}
        onRunStatusChange={handleRunStatusChange}
        onPageChange={setRunsPage}
        onRefresh={() => void runs.refetch()}
        onViewRun={setDetailRun}
      />

      <IngestionPanel
        id="ingestion-queue"
        title="Ingestion queue"
        description="Jobs waiting for or currently held by the ingestion worker."
        jobs={queue.data?.jobs ?? []}
        total={queue.data?.count ?? 0}
        page={queuePage}
        pageSize={JOB_PAGE_SIZE}
        isLoading={queue.isLoading}
        isError={queue.isError}
        isFetching={queue.isFetching}
        error={queue.error}
        emptyTitle="The ingestion queue is empty"
        emptyDescription="Scraped notices are queued here before ClamAV scanning, parsing and persistence."
        actionsDisabled={actionState !== null}
        onPageChange={setQueuePage}
        toolbar={
          <div className="space-y-1">
            <label htmlFor="queue-status" className="text-sm text-muted-foreground">
              Status
            </label>
            <Select
              value={queueStatus ?? 'ALL'}
              onValueChange={(value) => {
                setQueueStatus(value === 'ALL' ? null : (value as 'pending' | 'processing'))
                setQueuePage(0)
              }}
            >
              <SelectTrigger id="queue-status" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All queue states</SelectItem>
                <SelectItem value="pending">Pending</SelectItem>
                <SelectItem value="processing">Processing</SelectItem>
              </SelectContent>
            </Select>
          </div>
        }
      />

      <IngestionPanel
        id="quarantined-tenders"
        title="Quarantined tenders"
        description="Malware-positive ingestions stopped before Docling or pyGAEB. Retrying downloads and rescans the attachment."
        jobs={quarantine.data?.jobs ?? []}
        total={quarantine.data?.count ?? 0}
        page={quarantinePage}
        pageSize={JOB_PAGE_SIZE}
        isLoading={quarantine.isLoading}
        isError={quarantine.isError}
        isFetching={quarantine.isFetching}
        error={quarantine.error}
        emptyTitle="No quarantined tenders"
        emptyDescription="ClamAV has not rejected any queued attachment."
        actionsDisabled={actionState !== null}
        onPageChange={setQuarantinePage}
        onRetry={(job) => setActionState({ job, action: 'retry' })}
        onDiscard={(job) => setActionState({ job, action: 'discard' })}
      />

      <IngestionPanel
        id="ingestion-dlq"
        title="Dead-letter queue"
        description="Terminal parsing or processing failures. Retry after fixing the source or upstream service; discard to close them out."
        jobs={deadLetter.data?.jobs ?? []}
        total={deadLetter.data?.count ?? 0}
        page={dlqPage}
        pageSize={JOB_PAGE_SIZE}
        isLoading={deadLetter.isLoading}
        isError={deadLetter.isError}
        isFetching={deadLetter.isFetching}
        error={deadLetter.error}
        emptyTitle="No dead-letter jobs"
        emptyDescription="Every failed ingestion still has retry budget or was discarded."
        actionsDisabled={actionState !== null}
        onPageChange={setDlqPage}
        onRetry={(job) => setActionState({ job, action: 'retry' })}
        onDiscard={(job) => setActionState({ job, action: 'discard' })}
      />

      {queue.isError || quarantine.isError || deadLetter.isError ? (
        <p className="sr-only" role="alert">
          Some ingestion tables are unavailable. Use the error messages above.
        </p>
      ) : null}

      {probeOpen ? (
        <ProbePortalDialog
          portals={portals.data?.portals ?? []}
          onClose={() => setProbeOpen(false)}
        />
      ) : null}

      {manualMode !== null ? (
        <ManualWorkerDialog
          mode={manualMode}
          portals={portals.data?.portals ?? []}
          onClose={() => setManualMode(null)}
        />
      ) : null}

      {detailRun !== null ? (
        <WorkerRunDetailSheet run={detailRun} onClose={() => setDetailRun(null)} />
      ) : null}

      {actionState !== null ? (
        <IngestionActionDialog
          key={`${actionState.job.id}-${actionState.action}`}
          job={actionState.job}
          action={actionState.action}
          onClose={() => setActionState(null)}
          onCompleted={() => {
            setActionState(null)
            refreshIngestion()
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
