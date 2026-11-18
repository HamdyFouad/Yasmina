import { api } from '@/lib/api-client'
import type { NotificationsResponse } from '@/types/domain'

export async function fetchNotifications(): Promise<NotificationsResponse> {
  const response = await api.get<NotificationsResponse>('/ops/notifications')
  return response.data
}
