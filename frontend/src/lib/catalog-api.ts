import { api } from '@/lib/api-client'
import type {
  CatalogOverride,
  CatalogOverrideListResponse,
  CatalogOverrideRequest,
} from '@/types/domain'

/** Fields the backend allows operators to pin against future catalogue imports. */
export const CATALOG_OVERRIDE_FIELDS = [
  'description',
  'material',
  'color',
  'glove_size',
  'unit',
  'unit_price_net',
  'pack_size',
  'attributes',
  'embedding',
  'embedding_model',
] as const

export type CatalogOverrideField = (typeof CATALOG_OVERRIDE_FIELDS)[number]

export async function fetchCatalogOverrides(params: {
  supplier?: string
  articleNumber?: string
  limit?: number
  offset?: number
} = {}): Promise<CatalogOverrideListResponse> {
  const response = await api.get<CatalogOverrideListResponse>('/admin/catalog/overrides', {
    params: {
      ...(params.supplier ? { supplier: params.supplier } : {}),
      ...(params.articleNumber ? { article_number: params.articleNumber } : {}),
      ...(params.limit !== undefined ? { limit: params.limit } : {}),
      ...(params.offset !== undefined ? { offset: params.offset } : {}),
    },
  })
  return response.data
}

export async function updateCatalogOverrides(
  payload: CatalogOverrideRequest,
): Promise<CatalogOverride> {
  const response = await api.post<{ override: CatalogOverride }>(
    '/admin/catalog/overrides',
    payload,
  )
  return response.data.override
}
