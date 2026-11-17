import { api } from '@/lib/api-client'
import type {
  ManualSupplierQuoteRequest,
  RfqDispatchRequest,
  RfqDispatchResponse,
  SupplierQuoteListResponse,
  SupplierQuoteResponse,
} from '@/types/domain'

export async function fetchSupplierQuotes(
  tenderId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<SupplierQuoteListResponse> {
  const response = await api.get<SupplierQuoteListResponse>('/supplier-quotes', {
    params: {
      tender_id: tenderId,
      ...(params.limit !== undefined ? { limit: params.limit } : {}),
      ...(params.offset !== undefined ? { offset: params.offset } : {}),
    },
  })
  return response.data
}

export async function fetchSupplierQuote(quoteId: string): Promise<SupplierQuoteResponse> {
  const response = await api.get<SupplierQuoteResponse>(`/supplier-quotes/${quoteId}`)
  return response.data
}

export async function createSupplierQuote(
  payload: ManualSupplierQuoteRequest,
): Promise<SupplierQuoteResponse> {
  const response = await api.post<SupplierQuoteResponse>('/supplier-quotes', payload)
  return response.data
}

export async function dispatchRfq(
  payload: RfqDispatchRequest,
  idempotencyKey: string,
): Promise<RfqDispatchResponse> {
  const response = await api.post<RfqDispatchResponse>('/integrations/rfq/dispatch', payload, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  return response.data
}
