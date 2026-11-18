import { api } from '@/lib/api-client'
import type {
  AgingReport,
  ReportingMarginsResponse,
  ReportingSummary,
} from '@/types/domain'

export async function fetchReportingSummary(): Promise<ReportingSummary> {
  const response = await api.get<ReportingSummary>('/reporting/summary')
  return response.data
}

export async function fetchReportingMargins(): Promise<ReportingMarginsResponse> {
  const response = await api.get<ReportingMarginsResponse>('/reporting/margins')
  return response.data
}

export async function fetchReportingAging(): Promise<AgingReport> {
  const response = await api.get<AgingReport>('/reporting/aging')
  return response.data
}
