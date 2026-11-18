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
import { DeactivateDialog } from '@/features/master-data/deactivate-dialog'
import { SupplierDialog } from '@/features/master-data/supplier-dialog'
import { apiErrorMessage } from '@/lib/api-error'
import { deactivateSupplier, fetchSuppliers } from '@/lib/master-data-api'
import type { Supplier } from '@/types/domain'

const PAGE_SIZE = 20

export function SuppliersPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = React.useState('')
  const deferredSearch = React.useDeferredValue(search)
  const [activeFilter, setActiveFilter] = React.useState<'all' | 'active' | 'inactive'>('all')
  const [page, setPage] = React.useState(0)
  const [editing, setEditing] = React.useState<Supplier | null | undefined>(undefined)
  const [deactivating, setDeactivating] = React.useState<Supplier | null>(null)
  const captionId = React.useId()

  const active = activeFilter === 'all' ? undefined : activeFilter === 'active'
  const suppliersQuery = useQuery({
    queryKey: ['master-data', 'suppliers', { page, deferredSearch, active }],
    queryFn: () =>
      fetchSuppliers({
        ...(active !== undefined ? { active } : {}),
        search: deferredSearch.trim(),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  })

  const deactivate = useMutation({
    mutationFn: (supplier: Supplier) => deactivateSupplier(supplier.id),
    onSuccess: async (supplier) => {
      toast.success('Supplier deactivated', { description: supplier.name })
      setDeactivating(null)
      if (suppliers.length === 1 && page > 0) {
        setPage((current) => Math.max(0, current - 1))
      }
      await queryClient.invalidateQueries({ queryKey: ['master-data', 'suppliers'] })
    },
  })

  const suppliers = suppliersQuery.data?.suppliers ?? []
  const total = suppliersQuery.data?.count ?? 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Suppliers"
        description="Supplier directory, contacts, commercial terms and RFQ address-book defaults."
        actions={
          <Button type="button" size="sm" onClick={() => setEditing(null)}>
            <Plus />
            Create supplier
          </Button>
        }
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="supplier-search">Search</Label>
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              id="supplier-search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(0)
              }}
              placeholder="Name, legal name or e-mail"
              className="w-72 pl-8"
            />
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="supplier-active-filter">Status</Label>
          <Select
            value={activeFilter}
            onValueChange={(value) => {
              setActiveFilter(value as 'all' | 'active' | 'inactive')
              setPage(0)
            }}
          >
            <SelectTrigger id="supplier-active-filter" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All suppliers</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="inactive">Inactive</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <p className="ml-auto text-xs text-muted-foreground">
          {total.toLocaleString('de-DE')} supplier(s)
        </p>
      </div>

      {suppliersQuery.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : suppliersQuery.isError ? (
        <Card>
          <CardContent role="alert" className="p-5 text-sm text-destructive">
            {apiErrorMessage(suppliersQuery.error)}
          </CardContent>
        </Card>
      ) : suppliers.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No suppliers found"
          description="Create a supplier or adjust the search/status filter."
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
                Suppliers with primary contact, location, terms, status and actions
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  {['Supplier', 'Primary contact', 'Location', 'Terms', 'Status', 'Actions'].map(
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
                {suppliers.map((supplier) => (
                  <tr key={supplier.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                    <th scope="row" className="max-w-72 px-3 py-3 text-left align-top">
                      <span className="block font-medium">{supplier.name}</span>
                      {supplier.legal_name ? (
                        <span className="block text-xs text-muted-foreground">
                          {supplier.legal_name}
                        </span>
                      ) : null}
                      {supplier.email ? (
                        <span className="block text-xs text-muted-foreground">
                          {supplier.email}
                        </span>
                      ) : null}
                    </th>
                    <td data-label="Primary contact" className="px-3 py-3 align-top">
                      {supplier.primary_contact_name ?? '—'}
                      {supplier.primary_contact_email ? (
                        <span className="block text-xs text-muted-foreground">
                          {supplier.primary_contact_email}
                        </span>
                      ) : null}
                      <span className="mt-1 block text-[11px] text-muted-foreground">
                        {supplier.contact_count} contact(s)
                      </span>
                    </td>
                    <td data-label="Location" className="px-3 py-3 align-top">
                      {supplier.city ?? '—'}
                      {supplier.country_code ? (
                        <span className="block text-xs text-muted-foreground">
                          {supplier.country_code}
                        </span>
                      ) : null}
                    </td>
                    <td data-label="Terms" className="max-w-64 px-3 py-3 align-top text-xs">
                      {supplier.payment_terms ?? '—'}
                      <span className="mt-1 block text-[11px] text-muted-foreground">
                        {supplier.delivery_days !== null
                          ? `${supplier.delivery_days} delivery day(s)`
                          : 'No delivery time'}
                      </span>
                    </td>
                    <td data-label="Status" className="px-3 py-3 align-top">
                      <StatusBadge status={supplier.is_active ? 'ACTIVE' : 'INACTIVE'} />
                    </td>
                    <td data-label="Actions" className="whitespace-nowrap px-3 py-3 text-right align-top">
                      <div className="flex flex-wrap justify-end gap-1">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={`Edit supplier ${supplier.name}`}
                          onClick={() => setEditing(supplier)}
                        >
                          <Pencil />
                          Edit
                        </Button>
                        {supplier.is_active ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            aria-label={`Deactivate supplier ${supplier.name}`}
                            onClick={() => setDeactivating(supplier)}
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
        label="suppliers"
        total={total}
        page={page}
        pageSize={PAGE_SIZE}
        shown={suppliers.length}
        isFetching={suppliersQuery.isFetching}
        onPageChange={setPage}
      />

      {editing !== undefined ? (
        <SupplierDialog supplier={editing} onClose={() => setEditing(undefined)} />
      ) : null}
      {deactivating !== null ? (
        <DeactivateDialog
          title={`Deactivate ${deactivating.name}?`}
          description="The supplier remains in historical quotes but is hidden from active RFQ selection."
          isPending={deactivate.isPending}
          error={deactivate.error}
          onClose={() => setDeactivating(null)}
          onConfirm={() => deactivate.mutate(deactivating)}
        />
      ) : null}
    </div>
  )
}
