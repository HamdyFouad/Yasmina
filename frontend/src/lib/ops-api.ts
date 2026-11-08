import { api } from '@/lib/api-client'
import type { ActivityResponse, OpsSummary, OutboxStatus } from '@/types/domain'

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
