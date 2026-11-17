import { api } from '@/lib/api-client'
import { filenameFromDisposition, saveBlob } from '@/lib/download'
import type {
  ApproveOfferPayload,
  ApproveOfferResponse,
  CustomerOfferStatus,
  MatchResponse,
  OfferDetailResponse,
  OfferListResponse,
  PricingResponse,
  TenderWorkflowResponse,
} from '@/types/domain'

export async function fetchOffers(
  params: {
    limit?: number
    offset?: number
    status?: CustomerOfferStatus
    tenderId?: string
    owner?: string
  } = {},
): Promise<OfferListResponse> {
  const response = await api.get<OfferListResponse>('/offers', {
    params: {
      ...(params.limit !== undefined ? { limit: params.limit } : {}),
      ...(params.offset !== undefined ? { offset: params.offset } : {}),
      ...(params.status !== undefined ? { status: params.status } : {}),
      ...(params.tenderId !== undefined ? { tender_id: params.tenderId } : {}),
      ...(params.owner !== undefined ? { owner: params.owner } : {}),
    },
  })
  return response.data
}

export async function fetchOffer(offerId: string): Promise<OfferDetailResponse> {
  const response = await api.get<OfferDetailResponse>(`/offers/${offerId}`)
  return response.data
}

export async function approveOffer(payload: ApproveOfferPayload): Promise<ApproveOfferResponse> {
  const response = await api.post<ApproveOfferResponse>('/approvals/approve', payload)
  return response.data
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

export async function runTenderWorkflow(
  tenderId: string,
  payload: {
    supplier_quotes: Record<string, unknown>[]
    offer_request: Record<string, unknown>
  },
): Promise<TenderWorkflowResponse> {
  const response = await api.post<TenderWorkflowResponse>(
    `/tenders/${tenderId}/workflow`,
    payload,
  )
  return response.data
}

export async function downloadX84Bid(offerId: string, fallbackFilename: string): Promise<void> {
  const response = await api.post<Blob>(`/offers/${offerId}/x84`, null, {
    responseType: 'blob',
  })
  const filename = filenameFromDisposition(
    response.headers['content-disposition'],
    fallbackFilename || 'bid.x84',
  )
  saveBlob(response.data, filename)
}
