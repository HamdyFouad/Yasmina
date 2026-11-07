import { api } from '@/lib/api-client'
import { filenameFromDisposition, saveBlob } from '@/lib/download'
import type {
  InvoiceDetailResponse,
  InvoiceDocumentFormat,
  InvoiceListResponse,
  InvoicePaymentRequest,
  InvoicePaymentResponse,
  InvoiceStatus,
} from '@/types/domain'

export async function fetchInvoices(params: {
  limit?: number
  offset?: number
  status?: InvoiceStatus
} = {}): Promise<InvoiceListResponse> {
  const response = await api.get<InvoiceListResponse>('/invoices', {
    params: {
      ...(params.limit !== undefined ? { limit: params.limit } : {}),
      ...(params.offset !== undefined ? { offset: params.offset } : {}),
      ...(params.status !== undefined ? { status: params.status } : {}),
    },
  })
  return response.data
}

export async function fetchInvoice(invoiceId: string): Promise<InvoiceDetailResponse> {
  const response = await api.get<InvoiceDetailResponse>(`/invoices/${invoiceId}`)
  return response.data
}

export async function recordInvoicePayment(
  invoiceId: string,
  payload: InvoicePaymentRequest,
): Promise<InvoicePaymentResponse> {
  const response = await api.post<InvoicePaymentResponse>(
    `/invoices/${invoiceId}/payments`,
    payload,
  )
  return response.data
}

/** Fetch the generated ZUGFeRD PDF or XRechnung XML and save it locally. */
export async function downloadInvoiceDocument(
  invoiceId: string,
  format: InvoiceDocumentFormat,
  fallbackFilename: string,
): Promise<void> {
  const response = await api.get<Blob>(`/invoices/${invoiceId}/documents/${format}`, {
    responseType: 'blob',
  })
  const filename = filenameFromDisposition(
    response.headers['content-disposition'],
    fallbackFilename,
  )
  saveBlob(response.data, filename)
}
