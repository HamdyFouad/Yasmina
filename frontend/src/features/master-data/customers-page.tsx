import * as React from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Pencil, Plus, Search } from 'lucide-react'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { TablePagination } from '@/components/table-pagination'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { CustomerDialog } from '@/features/master-data/customer-dialog'
import { DeactivateDialog } from '@/features/master-data/deactivate-dialog'
import { apiErrorMessage } from '@/lib/api-error'
import { deactivateCustomer, fetchCustomers } from '@/lib/master-data-api'
import type { Customer } from '@/types/domain'

const PAGE_SIZE = 20

export function CustomersPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const deferredSearch = React.useDeferredValue(search)
  const [activeFilter, setActiveFilter] = React.useState<'all' | 'active' | 'inactive'>('all')
  const [page, setPage] = React.useState(0)
  const [editing, setEditing] = React.useState<Customer | null | undefined>(undefined)
  const [deactivating, setDeactivating] = React.useState<Customer | null>(null)
  const captionId = React.useId()

  const active = activeFilter === 'all' ? undefined : activeFilter === 'active'
  const customersQuery = useQuery({
    queryKey: ['master-data', 'customers', { page, deferredSearch, active }],
    queryFn: () =>
      fetchCustomers({
        ...(active !== undefined ? { active } : {}),
        search: deferredSearch.trim(),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  })

  const deactivate = useMutation({
    mutationFn: (customer: Customer) => deactivateCustomer(customer.id),
    onSuccess: async (customer) => {
      toast.success('Customer deactivated', { description: customer.name })
      setDeactivating(null)
      if (customers.length === 1 && page > 0) {
        setPage((current) => Math.max(0, current - 1))
      }
      await queryClient.invalidateQueries({ queryKey: ['master-data', 'customers'] })
    },
  })

  const customers = customersQuery.data?.customers ?? []
  const total = customersQuery.data?.count ?? 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Customers"
        description="Customer billing master data used to prefill e-invoice addresses, VAT IDs, Leitweg-IDs and terms."
        actions={
          <Button type="button" size="sm" onClick={() => setEditing(null)}>
            <Plus />
            Create customer
          </Button>
        }
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="customer-search">Search</Label>
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              id="customer-search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(0)
              }}
              placeholder="Name, city or e-mail"
              className="w-72 pl-8"
            />
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="customer-active-filter">Status</Label>
          <Select
            value={activeFilter}
            onValueChange={(value) => {
              setActiveFilter(value as 'all' | 'active' | 'inactive')
              setPage(0)
            }}
          >
            <SelectTrigger id="customer-active-filter" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All customers</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="inactive">Inactive</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <p className="ml-auto text-xs text-muted-foreground">
          {total.toLocaleString('de-DE')} customer(s)
        </p>
      </div>

      {customersQuery.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : customersQuery.isError ? (
        <Card>
          <CardContent role="alert" className="p-5 text-sm text-destructive">
            {apiErrorMessage(customersQuery.error)}
          </CardContent>
        </Card>
      ) : customers.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No customers found"
          description="Create a customer or adjust the search/status filter."
        />
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            tabIndex={0}
            className="stacked-table max-h-[62vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:ring-2 focus-visible:ring-ring"
          >
            <table className="w-full min-w-[880px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                Customers with billing address, VAT ID, Leitweg-ID, terms, status and actions
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  {['Customer', 'Billing address', 'VAT / Leitweg', 'Terms', 'Status', 'Actions'].map(
                    (label) => (
                      <th
                        key={label}
                        scope="col"
                        className={`h-10 px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground ${
                          label === 'Actions' ? 'text-right' : 'text-left'
                        }`}
                      >
                        {label}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {customers.map((customer) => (
                  <tr key={customer.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                    <th scope="row" className="max-w-72 px-3 py-3 text-left align-top">
                      <span className="block font-medium">{customer.name}</span>
                      {customer.legal_name ? (
                        <span className="block text-xs text-muted-foreground">
                          {customer.legal_name}
                        </span>
                      ) : null}
                      {customer.contact_email ? (
                        <span className="block text-xs text-muted-foreground">
                          {customer.contact_email}
                        </span>
                      ) : null}
                    </th>
                    <td data-label="Billing address" className="px-3 py-3 align-top">
                      {customer.street}
                      <span className="block text-xs text-muted-foreground">
                        {customer.postal_code} {customer.city} · {customer.country_code}
                      </span>
                    </td>
                    <td data-label="VAT / Leitweg" className="px-3 py-3 align-top text-xs">
                      <span className="block">VAT: {customer.vat_id ?? '—'}</span>
                      <span className="block">Leitweg: {customer.leitweg_id ?? '—'}</span>
                    </td>
                    <td data-label="Terms" className="max-w-64 px-3 py-3 align-top text-xs">
                      {customer.payment_terms ?? '—'}
                    </td>
                    <td data-label="Status" className="px-3 py-3 align-top">
                      <StatusBadge status={customer.is_active ? 'ACTIVE' : 'INACTIVE'} />
                    </td>
                    <td data-label="Actions" className="whitespace-nowrap px-3 py-3 text-right align-top">
                      <div className="flex flex-wrap justify-end gap-1">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={`Edit customer ${customer.name}`}
                          onClick={() => setEditing(customer)}
                        >
                          <Pencil />
                          Edit
                        </Button>
                        {customer.is_active ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            aria-label={`Deactivate customer ${customer.name}`}
                            onClick={() => setDeactivating(customer)}
                          >
                            Deactivate
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <TablePagination
        label="customers"
        total={total}
        page={page}
        pageSize={PAGE_SIZE}
        shown={customers.length}
        isFetching={customersQuery.isFetching}
        onPageChange={setPage}
      />

      {editing !== undefined ? (
        <CustomerDialog customer={editing} onClose={() => setEditing(undefined)} />
      ) : null}
      {deactivating !== null ? (
        <DeactivateDialog
          title={`Deactivate ${deactivating.name}?`}
          description="The customer remains in historical invoices but is hidden from active billing selection."
          isPending={deactivate.isPending}
          error={deactivate.error}
          onClose={() => setDeactivating(null)}
          onConfirm={() => deactivate.mutate(deactivating)}
        />
      ) : null}
    </div>
  )
}
