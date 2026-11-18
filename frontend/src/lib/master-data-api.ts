import { api } from '@/lib/api-client'
import type {
  Customer,
  CustomersResponse,
  Supplier,
  SupplierContact,
  SuppliersResponse,
} from '@/types/domain'

export interface SupplierContactPayload {
  id?: string
  name: string
  role?: string | null
  email?: string | null
  phone?: string | null
  is_primary: boolean
}

export interface SupplierPayload {
  name: string
  legal_name?: string | null
  email?: string | null
  phone?: string | null
  street?: string | null
  postal_code?: string | null
  city?: string | null
  country_code: string
  payment_terms?: string | null
  delivery_days?: number | null
  notes?: string | null
  is_active: boolean
  contacts: SupplierContactPayload[]
}

export interface CustomerPayload {
  name: string
  legal_name?: string | null
  street: string
  postal_code: string
  city: string
  country_code: string
  vat_id?: string | null
  leitweg_id?: string | null
  payment_terms?: string | null
  contact_name?: string | null
  contact_email?: string | null
  phone?: string | null
  notes?: string | null
  is_active: boolean
}

export async function fetchSuppliers(
  params: { active?: boolean; search?: string; limit?: number; offset?: number } = {},
): Promise<SuppliersResponse> {
  const response = await api.get<SuppliersResponse>('/suppliers', {
    params: {
      ...(params.active !== undefined ? { active: params.active } : {}),
      ...(params.search ? { search: params.search } : {}),
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return response.data
}

export async function fetchSupplier(supplierId: string): Promise<Supplier> {
  const response = await api.get<{ supplier: Supplier }>(
    `/suppliers/${encodeURIComponent(supplierId)}`,
  )
  return response.data.supplier
}

export async function createSupplier(payload: SupplierPayload): Promise<Supplier> {
  const response = await api.post<{ supplier: Supplier }>('/suppliers', payload)
  return response.data.supplier
}

export async function updateSupplier(
  supplierId: string,
  payload: SupplierPayload,
): Promise<Supplier> {
  const response = await api.put<{ supplier: Supplier }>(
    `/suppliers/${encodeURIComponent(supplierId)}`,
    payload,
  )
  return response.data.supplier
}

export async function deactivateSupplier(supplierId: string): Promise<Supplier> {
  const response = await api.delete<{ supplier: Supplier }>(
    `/suppliers/${encodeURIComponent(supplierId)}`,
  )
  return response.data.supplier
}

export async function fetchCustomers(
  params: { active?: boolean; search?: string; limit?: number; offset?: number } = {},
): Promise<CustomersResponse> {
  const response = await api.get<CustomersResponse>('/customers', {
    params: {
      ...(params.active !== undefined ? { active: params.active } : {}),
      ...(params.search ? { search: params.search } : {}),
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return response.data
}

export async function fetchCustomer(customerId: string): Promise<Customer> {
  const response = await api.get<{ customer: Customer }>(
    `/customers/${encodeURIComponent(customerId)}`,
  )
  return response.data.customer
}

export async function createCustomer(payload: CustomerPayload): Promise<Customer> {
  const response = await api.post<{ customer: Customer }>('/customers', payload)
  return response.data.customer
}

export async function updateCustomer(
  customerId: string,
  payload: CustomerPayload,
): Promise<Customer> {
  const response = await api.put<{ customer: Customer }>(
    `/customers/${encodeURIComponent(customerId)}`,
    payload,
  )
  return response.data.customer
}

export async function deactivateCustomer(customerId: string): Promise<Customer> {
  const response = await api.delete<{ customer: Customer }>(
    `/customers/${encodeURIComponent(customerId)}`,
  )
  return response.data.customer
}

export function toSupplierPayload(supplier: Supplier): SupplierPayload {
  return {
    name: supplier.name,
    legal_name: supplier.legal_name,
    email: supplier.email,
    phone: supplier.phone,
    street: supplier.street,
    postal_code: supplier.postal_code,
    city: supplier.city,
    country_code: supplier.country_code,
    payment_terms: supplier.payment_terms,
    delivery_days: supplier.delivery_days,
    notes: supplier.notes,
    is_active: supplier.is_active,
    contacts: supplier.contacts.map((contact: SupplierContact) => ({
      id: contact.id,
      name: contact.name,
      role: contact.role,
      email: contact.email,
      phone: contact.phone,
      is_primary: contact.is_primary,
    })),
  }
}
