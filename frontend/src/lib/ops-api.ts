import { api } from '@/lib/api-client'
import type {
  ActivityResponse,
  OpsSummary,
  OutboxJobsResponse,
  OutboxRetryResponse,
  OutboxStatus,
  QuotationSyncJob,
  QuotationSyncStatus,
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
