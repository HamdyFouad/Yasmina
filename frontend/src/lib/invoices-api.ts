import { api } from '@/lib/api-client'
import type {
  InvoiceDetailResponse,
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
