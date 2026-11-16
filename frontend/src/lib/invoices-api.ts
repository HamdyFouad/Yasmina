import { api } from '@/lib/api-client'
import { filenameFromDisposition, saveBlob } from '@/lib/download'
import type {
  CreditNote,
  InvoiceCancelRequest,
  InvoiceCancelResponse,
  InvoiceCreditNotesResponse,
  InvoiceCreditRequest,
  InvoiceCreditResponse,
  InvoiceDetailResponse,
  InvoiceDocumentFormat,
  InvoiceEmailRequest,
  InvoiceEmailResponse,
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

export async function fetchInvoiceCreditNotes(invoiceId: string): Promise<CreditNote[]> {
  const response = await api.get<InvoiceCreditNotesResponse>(
    `/invoices/${invoiceId}/credit-notes`,
  )
  return response.data.credit_notes
}

export async function issueInvoiceCredit(
  invoiceId: string,
  payload: InvoiceCreditRequest,
): Promise<InvoiceCreditResponse> {
  const response = await api.post<InvoiceCreditResponse>(
    `/invoices/${invoiceId}/credits`,
    payload,
  )
  return response.data
}

export async function cancelInvoice(
  invoiceId: string,
  payload: InvoiceCancelRequest,
): Promise<InvoiceCancelResponse> {
  const response = await api.post<InvoiceCancelResponse>(
    `/invoices/${invoiceId}/cancel`,
    payload,
  )
  return response.data
}

export async function emailInvoice(
  invoiceId: string,
  payload: InvoiceEmailRequest = {},
): Promise<InvoiceEmailResponse> {
  const response = await api.post<InvoiceEmailResponse>(
    `/invoices/${invoiceId}/email`,
    payload,
  )
  return response.data
}

export async function downloadCreditNoteDocument(
  invoiceId: string,
  creditNoteId: string,
  format: InvoiceDocumentFormat,
  fallbackFilename: string,
): Promise<void> {
  const response = await api.get<Blob>(
    `/invoices/${invoiceId}/credit-notes/${creditNoteId}/documents/${format}`,
    { responseType: 'blob' },
  )
  const filename = filenameFromDisposition(
    response.headers['content-disposition'],
    fallbackFilename,
  )
  saveBlob(response.data, filename)
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
