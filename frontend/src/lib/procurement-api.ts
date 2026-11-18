import { api } from '@/lib/api-client'
import type {
  AcceptedSupplierQuotesResponse,
  MarginSummariesResponse,
  MarginSummaryResponse,
  ProcurementHealth,
  PurchaseOrder,
  PurchaseOrderCreateRequest,
  PurchaseOrderResponse,
  PurchaseOrdersResponse,
  PurchaseOrderStatus,
  SupplierInvoice,
  SupplierInvoiceRequest,
  SupplierInvoiceResponse,
  SupplierInvoicesResponse,
} from '@/types/domain'

export async function fetchProcurementHealth(): Promise<ProcurementHealth> {
  const response = await api.get<ProcurementHealth>('/procurement/health')
  return response.data
}

export async function fetchAcceptedSupplierQuotes(): Promise<AcceptedSupplierQuotesResponse> {
  const response = await api.get<AcceptedSupplierQuotesResponse>('/procurement/accepted-quotes')
  return response.data
}

export async function fetchPurchaseOrders(params: {
  status?: PurchaseOrderStatus | null
  search?: string
  limit?: number
  offset?: number
} = {}): Promise<PurchaseOrdersResponse> {
  const response = await api.get<PurchaseOrdersResponse>('/procurement/purchase-orders', {
    params: {
      ...(params.status ? { status: params.status } : {}),
      ...(params.search ? { search: params.search } : {}),
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return response.data
}

export async function createPurchaseOrder(
  payload: PurchaseOrderCreateRequest,
): Promise<PurchaseOrder> {
  const response = await api.post<PurchaseOrderResponse>(
    '/procurement/purchase-orders',
    payload,
  )
  return response.data.purchase_order
}

export async function transitionPurchaseOrder(
  purchaseOrderId: string,
  status: PurchaseOrderStatus,
): Promise<PurchaseOrder> {
  const response = await api.post<PurchaseOrderResponse>(
    `/procurement/purchase-orders/${encodeURIComponent(purchaseOrderId)}/status`,
    { status },
  )
  return response.data.purchase_order
}

export async function fetchSupplierInvoices(params: {
  status?: string | null
  exceptionOnly?: boolean
  purchaseOrderId?: string | null
  search?: string
  limit?: number
  offset?: number
} = {}): Promise<SupplierInvoicesResponse> {
  const response = await api.get<SupplierInvoicesResponse>('/procurement/supplier-invoices', {
    params: {
      ...(params.status ? { status: params.status } : {}),
      ...(params.exceptionOnly ? { exception_only: true } : {}),
      ...(params.purchaseOrderId ? { purchase_order_id: params.purchaseOrderId } : {}),
      ...(params.search ? { search: params.search } : {}),
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return response.data
}

export async function createSupplierInvoice(
  payload: SupplierInvoiceRequest,
): Promise<SupplierInvoice> {
  const response = await api.post<SupplierInvoiceResponse>(
    '/procurement/supplier-invoices',
    payload,
  )
  return response.data.supplier_invoice
}

export async function rematchSupplierInvoice(
  invoiceId: string,
): Promise<SupplierInvoice> {
  const response = await api.post<SupplierInvoiceResponse>(
    `/procurement/supplier-invoices/${encodeURIComponent(invoiceId)}/rematch`,
  )
  return response.data.supplier_invoice
}

export async function fetchMarginSummaries(
  params: { limit?: number; offset?: number } = {},
): Promise<MarginSummariesResponse> {
  const response = await api.get<MarginSummariesResponse>('/procurement/margins', {
    params: {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return response.data
}

export async function fetchMarginSummary(offerId: string): Promise<MarginSummaryResponse> {
  const response = await api.get<MarginSummaryResponse>(
    `/procurement/margins/${encodeURIComponent(offerId)}`,
  )
  return response.data
}
