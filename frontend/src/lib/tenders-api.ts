import { api } from '@/lib/api-client'
import { filenameFromDisposition, saveBlob } from '@/lib/download'
import type {
  TenderDetailResponse,
  TenderListResponse,
  TenderOwnerResponse,
  TenderStatus,
  UploadTenderResponse,
} from '@/types/domain'

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

export async function uploadTender(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadTenderResponse> {
  const form = new FormData()
  form.append('file', file)
  const response = await api.post<UploadTenderResponse>('/tenders/upload', form, {
    onUploadProgress: (event) => {
      if (onProgress !== undefined && event.total !== undefined && event.total > 0) {
        onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)))
      }
    },
  })
  return response.data
}

export async function assignTenderOwner(
  tenderId: string,
  owner: string,
): Promise<TenderOwnerResponse> {
  const response = await api.patch<TenderOwnerResponse>(`/tenders/${tenderId}/owner`, {
    owner,
  })
  return response.data
}
