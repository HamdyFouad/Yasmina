import { api } from '@/lib/api-client'
import type {
  ActivityResponse,
  IngestionJob,
  IngestionJobsResponse,
  IngestionJobStatus,
  OpsSummary,
  OutboxJobsResponse,
  OutboxRetryResponse,
  OutboxStatus,
  PortalsResponse,
  ProbeReport,
  QuotationSyncJob,
  QuotationSyncStatus,
  ReleaseStaleLocksResponse,
  WorkerCommandResponse,
  WorkerRunsResponse,
  WorkerRunStatus,
  WorkerRunType,
} from '@/types/domain'

export async function fetchOpsSummary(): Promise<OpsSummary> {
  const response = await api.get<OpsSummary>('/ops/summary')
  return response.data
}

export async function fetchOpsActivity(limit = 20): Promise<ActivityResponse> {
  const response = await api.get<ActivityResponse>('/ops/activity', {
    params: { limit },
  })
  return response.data
}

export async function fetchOutboxStatus(): Promise<OutboxStatus> {
  const response = await api.get<OutboxStatus>('/ops/outbox-status')
  return response.data
}

export interface OutboxJobsParams {
  status?: QuotationSyncStatus | null
  limit?: number
  offset?: number
}

export async function fetchOutboxJobs(
  params: OutboxJobsParams = {},
): Promise<OutboxJobsResponse> {
  const response = await api.get<OutboxJobsResponse>('/ops/outbox', {
    params: {
      limit: params.limit ?? 25,
      offset: params.offset ?? 0,
      ...(params.status != null ? { status: params.status } : {}),
    },
  })
  return response.data
}

export async function retryOutboxJob(jobId: string): Promise<QuotationSyncJob> {
  const response = await api.post<OutboxRetryResponse>(
    `/ops/outbox/${encodeURIComponent(jobId)}/retry`,
  )
  return response.data.job
}

export async function fetchWorkerRuns(
  params: {
    limit?: number
    offset?: number
    runType?: WorkerRunType
    status?: WorkerRunStatus
  } = {},
): Promise<WorkerRunsResponse> {
  const response = await api.get<WorkerRunsResponse>('/ops/worker-runs', {
    params: {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
      ...(params.runType !== undefined ? { run_type: params.runType } : {}),
      ...(params.status !== undefined ? { status: params.status } : {}),
    },
  })
  return response.data
}

export async function fetchIngestionJobs(
  params: {
    limit?: number
    offset?: number
    status?: IngestionJobStatus
    quarantined?: boolean
    queue?: boolean
  } = {},
): Promise<IngestionJobsResponse> {
  const response = await api.get<IngestionJobsResponse>('/ops/ingestion', {
    params: {
      limit: params.limit ?? 200,
      offset: params.offset ?? 0,
      ...(params.status !== undefined ? { status: params.status } : {}),
      ...(params.quarantined !== undefined ? { quarantined: params.quarantined } : {}),
      ...(params.queue !== undefined ? { queue: params.queue } : {}),
    },
  })
  return response.data
}

export async function retryIngestionJob(jobId: string): Promise<IngestionJob> {
  const response = await api.post<{ job: IngestionJobsResponse['jobs'][number] }>(
    `/ops/ingestion/${encodeURIComponent(jobId)}/retry`,
  )
  return response.data.job
}

export async function discardIngestionJob(
  jobId: string,
  reason: string,
): Promise<IngestionJob> {
  const response = await api.post<{ job: IngestionJobsResponse['jobs'][number] }>(
    `/ops/ingestion/${encodeURIComponent(jobId)}/discard`,
    { reason },
  )
  return response.data.job
}

export async function fetchPortalTargets(): Promise<PortalsResponse> {
  const response = await api.get<PortalsResponse>('/ops/portals')
  return response.data
}

export async function probePortalTarget(portalId: string, maxPages = 1): Promise<ProbeReport> {
  const response = await api.post<ProbeReport>(
    `/ops/portals/${encodeURIComponent(portalId)}/probe`,
    null,
    { params: { max_pages: maxPages } },
  )
  return response.data
}

export async function triggerScrapeRun(
  portal: string,
  maxPages: number,
): Promise<WorkerCommandResponse> {
  const response = await api.post<WorkerCommandResponse>('/ops/worker-runs/scrape', {
    portal,
    max_pages: maxPages,
  })
  return response.data
}

export async function triggerIngestionRun(limit: number): Promise<WorkerCommandResponse> {
  const response = await api.post<WorkerCommandResponse>('/ops/worker-runs/ingestion', {
    limit,
  })
  return response.data
}

export async function releaseStaleLocks(
  olderThanSeconds: number,
): Promise<ReleaseStaleLocksResponse> {
  const response = await api.post<ReleaseStaleLocksResponse>('/ops/ingestion/release-stale', {
    older_than_seconds: olderThanSeconds,
  })
  return response.data
}
