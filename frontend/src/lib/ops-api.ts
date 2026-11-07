import { api } from '@/lib/api-client'
import type { ActivityResponse, OpsSummary } from '@/types/domain'

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
