import { api } from '@/lib/api-client'
import { filenameFromDisposition, saveBlob } from '@/lib/download'
import type { TenderDetailResponse, TenderListResponse, TenderStatus } from '@/types/domain'

export async function fetchTenders(
  params: {
    limit?: number
    offset?: number
    status?: TenderStatus
  } = {},
): Promise<TenderListResponse> {
  const response = await api.get<TenderListResponse>('/tenders', {
    params: {
      ...(params.limit !== undefined ? { limit: params.limit } : {}),
      ...(params.offset !== undefined ? { offset: params.offset } : {}),
      ...(params.status !== undefined ? { status: params.status } : {}),
    },
  })
  return response.data
}

export async function fetchTender(tenderId: string): Promise<TenderDetailResponse> {
  const response = await api.get<TenderDetailResponse>(`/tenders/${tenderId}`)
  return response.data
}

export async function downloadTenderDocument(
  tenderId: string,
  fallbackFilename: string,
): Promise<void> {
  const response = await api.get<Blob>(`/tenders/${tenderId}/document`, {
    responseType: 'blob',
  })
  const filename = filenameFromDisposition(
    response.headers['content-disposition'],
    fallbackFilename || 'tender-document',
  )
  saveBlob(response.data, filename)
}
