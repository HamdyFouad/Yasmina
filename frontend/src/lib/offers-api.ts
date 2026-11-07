import { api } from '@/lib/api-client'
import type {
  CustomerOffer,
  CustomerOfferStatus,
  MatchResponse,
  OfferListResponse,
  PricingResponse,
} from '@/types/domain'

export async function fetchOffers(params: {
  limit?: number
  offset?: number
  status?: CustomerOfferStatus
} = {}): Promise<OfferListResponse> {
  const response = await api.get<OfferListResponse>('/offers', {
    params: {
      ...(params.limit !== undefined ? { limit: params.limit } : {}),
      ...(params.offset !== undefined ? { offset: params.offset } : {}),
      ...(params.status !== undefined ? { status: params.status } : {}),
    },
  })
  return response.data
}

export async function fetchOffer(offerId: string): Promise<CustomerOffer> {
  const response = await api.get<{ offer: CustomerOffer }>(`/offers/${offerId}`)
  return response.data.offer
}

export async function matchTender(
  tenderId: string,
  limit = 5,
): Promise<MatchResponse> {
  const response = await api.post<MatchResponse>(`/tenders/${tenderId}/match`, { limit })
  return response.data
}

export async function priceTender(
  tenderId: string,
  payload: {
    supplier_quotes: Record<string, unknown>[]
    offer_request: Record<string, unknown>
  },
): Promise<PricingResponse> {
  const response = await api.post<PricingResponse>(`/tenders/${tenderId}/price`, payload)
  return response.data
}
