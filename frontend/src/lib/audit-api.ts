import { api } from '@/lib/api-client'
import type {
  AuditEventTypesResponse,
  AuditExportFormat,
  AuditLogsResponse,
} from '@/types/domain'

export interface AuditFilters {
  eventType?: string | null
  actor?: string
  entityType?: string
  entityId?: string
  search?: string
  dateFrom?: string
  dateTo?: string
}

function params(filters: AuditFilters): Record<string, string> {
  return {
    ...(filters.eventType ? { event_type: filters.eventType } : {}),
    ...(filters.actor?.trim() ? { actor: filters.actor.trim() } : {}),
    ...(filters.entityType?.trim() ? { entity_type: filters.entityType.trim() } : {}),
    ...(filters.entityId?.trim() ? { entity_id: filters.entityId.trim() } : {}),
    ...(filters.search?.trim() ? { search: filters.search.trim() } : {}),
    ...(filters.dateFrom ? { date_from: filters.dateFrom } : {}),
    ...(filters.dateTo ? { date_to: filters.dateTo } : {}),
  }
}

export async function fetchAuditLogs(
  filters: AuditFilters,
  page: { limit: number; offset: number },
): Promise<AuditLogsResponse> {
  const response = await api.get<AuditLogsResponse>('/audit/logs', {
    params: { ...params(filters), limit: page.limit, offset: page.offset },
  })
  return response.data
}

export async function fetchAuditEventTypes(): Promise<AuditEventTypesResponse> {
  const response = await api.get<AuditEventTypesResponse>('/audit/event-types')
  return response.data
}

export async function downloadAuditExport(
  format: AuditExportFormat,
  filters: AuditFilters,
): Promise<{ blob: Blob; filename: string }> {
  const response = await api.get<Blob>('/audit/logs/export', {
    params: { format, ...params(filters) },
    responseType: 'blob',
  })
  const disposition = response.headers['content-disposition']
  const match = typeof disposition === 'string' ? /filename="([^"]+)"/.exec(disposition) : null
  return {
    blob: response.data,
    filename: match?.[1] ?? `yasmina-audit-logs.${format}`,
  }
}

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
