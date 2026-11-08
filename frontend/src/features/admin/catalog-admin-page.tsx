import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Pin,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiErrorMessage } from '@/lib/api-error'
import {
  CATALOG_OVERRIDE_FIELDS,
  fetchCatalogOverrides,
  updateCatalogOverrides,
} from '@/lib/catalog-api'
import { formatCurrency, formatDateTime } from '@/lib/utils'
import type {
  CatalogOverride,
  CatalogOverrideMode,
  CatalogOverrideRequest,
} from '@/types/domain'

const PAGE_SIZE = 25

const MODE_OPTIONS: { value: CatalogOverrideMode; label: string }[] = [
  { value: 'add', label: 'Add pins' },
  { value: 'replace', label: 'Replace pins' },
  { value: 'clear', label: 'Clear pins' },
]

function isOverrideMode(value: string): value is CatalogOverrideMode {
  return MODE_OPTIONS.some((option) => option.value === value)
}

function OverrideDialog({
  open,
  onOpenChange,
  initial,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial: CatalogOverride | null
}) {
  const queryClient = useQueryClient()
  const [supplier, setSupplier] = React.useState(initial?.supplier_name ?? '')
  const [article, setArticle] = React.useState(initial?.article_number ?? '')
  const [mode, setMode] = React.useState<CatalogOverrideMode>('add')
  const [fields, setFields] = React.useState<string[]>(initial?.manual_overrides ?? [])

  const mutation = useMutation({
    mutationFn: (payload: CatalogOverrideRequest) => updateCatalogOverrides(payload),
    onSuccess: async (override) => {
      toast.success('Catalogue overrides updated', {
        description: `${override.supplier_name} · ${override.article_number}`,
      })
      await queryClient.invalidateQueries({ queryKey: ['catalog-overrides'] })
      onOpenChange(false)
    },
  })

  const toggleField = (field: string): void => {
    setFields((current) =>
      current.includes(field) ? current.filter((item) => item !== field) : [...current, field],
    )
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (mode !== 'clear' && fields.length === 0) {
      return
    }
    mutation.mutate({
      supplier_name: supplier.trim(),
      article_number: article.trim(),
      fields,
      mode,
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Manage catalogue pins</DialogTitle>
          <DialogDescription>
            Pin fields so future catalogue imports cannot overwrite operator-approved values.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-5" onSubmit={handleSubmit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="pin-supplier">Supplier</Label>
              <Input
                id="pin-supplier"
                value={supplier}
                onChange={(event) => setSupplier(event.target.value)}
                placeholder="Hygiene Gross GmbH"
                required
                disabled={mutation.isPending}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pin-article">Article number</Label>
              <Input
                id="pin-article"
                value={article}
                onChange={(event) => setArticle(event.target.value)}
                placeholder="NIT-M-100"
                required
                disabled={mutation.isPending}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="pin-mode">Mode</Label>
            <Select
              value={mode}
              onValueChange={(value) => {
                if (isOverrideMode(value)) {
                  setMode(value)
                }
              }}
            >
              <SelectTrigger id="pin-mode">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODE_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {mode === 'clear' ? (
              <p className="text-xs text-muted-foreground">
                Select fields to unpin, or leave all unchecked to clear every pin.
              </p>
            ) : null}
          </div>

          <div className="space-y-2">
            <Label>Fields</Label>
            <div className="flex flex-wrap gap-2">
              {CATALOG_OVERRIDE_FIELDS.map((field) => {
                const selected = fields.includes(field)
                return (
                  <Button
                    key={field}
                    type="button"
                    size="sm"
                    variant={selected ? 'default' : 'outline'}
                    onClick={() => toggleField(field)}
                    disabled={mutation.isPending}
                    aria-pressed={selected}
                  >
                    {selected ? <Pin /> : null}
                    {field}
                  </Button>
                )
              })}
            </div>
            {mode !== 'clear' && fields.length === 0 ? (
              <p className="text-xs text-destructive">Select at least one field to pin.</p>
            ) : null}
          </div>

          {mutation.isError ? (
            <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              {apiErrorMessage(mutation.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant={mode === 'clear' ? 'destructive' : 'default'}
              disabled={mutation.isPending || (mode !== 'clear' && fields.length === 0)}
            >
              {mutation.isPending ? <Loader2 className="animate-spin" /> : mode === 'clear' ? <Trash2 /> : <Pin />}
              {mode === 'clear' ? 'Apply clear' : 'Save pins'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function CatalogAdminPage() {
  const [supplierInput, setSupplierInput] = React.useState('')
  const [articleInput, setArticleInput] = React.useState('')
  const [filters, setFilters] = React.useState({ supplier: '', articleNumber: '' })
  const [page, setPage] = React.useState(0)
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [editing, setEditing] = React.useState<CatalogOverride | null>(null)

  const offset = page * PAGE_SIZE
  const overrides = useQuery({
    queryKey: ['catalog-overrides', { filters, offset }],
    queryFn: () =>
      fetchCatalogOverrides({
        ...(filters.supplier ? { supplier: filters.supplier } : {}),
        ...(filters.articleNumber ? { articleNumber: filters.articleNumber } : {}),
        limit: PAGE_SIZE,
        offset,
      }),
  })

  const total = overrides.data?.count ?? 0
  const rows = overrides.data?.overrides ?? []
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = offset + rows.length

  const openCreate = (): void => {
    setEditing(null)
    setDialogOpen(true)
  }

  const openEdit = (override: CatalogOverride): void => {
    setEditing(override)
    setDialogOpen(true)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Catalogue admin"
        description="Review, pin and clear manual merge overrides for supplier catalogue articles."
        actions={
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={overrides.isFetching}
              onClick={() => void overrides.refetch()}
            >
              {overrides.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Refresh
            </Button>
            <Button type="button" size="sm" onClick={openCreate}>
              <Pin />
              Pin fields
            </Button>
          </div>
        }
      />

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <form
            className="flex flex-1 flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              setFilters({
                supplier: supplierInput.trim(),
                articleNumber: articleInput.trim(),
              })
              setPage(0)
            }}
          >
            <div className="min-w-48 flex-1 space-y-1">
              <Label htmlFor="filter-supplier">Supplier</Label>
              <Input
                id="filter-supplier"
                value={supplierInput}
                onChange={(event) => setSupplierInput(event.target.value)}
                placeholder="Filter by supplier"
              />
            </div>
            <div className="min-w-40 flex-1 space-y-1">
              <Label htmlFor="filter-article">Article</Label>
              <Input
                id="filter-article"
                value={articleInput}
                onChange={(event) => setArticleInput(event.target.value)}
                placeholder="Filter by article number"
              />
            </div>
            <Button type="submit" variant="outline" size="sm">
              Apply filters
            </Button>
            {(filters.supplier || filters.articleNumber) !== '' ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSupplierInput('')
                  setArticleInput('')
                  setFilters({ supplier: '', articleNumber: '' })
                  setPage(0)
                }}
              >
                Clear
              </Button>
            ) : null}
          </form>
          <Badge variant="secondary" className="mb-0.5">
            {total.toLocaleString('de-DE')} pinned article(s)
          </Badge>
        </CardContent>
      </Card>

      {overrides.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : overrides.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <AlertTriangle className="h-6 w-6 text-destructive" />
            <div>
              <p className="text-sm font-medium">Could not load catalogue overrides</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(overrides.error)}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void overrides.refetch()}>
              <RefreshCw />
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="No catalogue pins"
          description="Pin a field when an imported supplier value has been verified and must not be overwritten by the next catalogue import."
          hint="Pins are stored as catalog_articles.manual_overrides."
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Article</TableHead>
                <TableHead>Pinned fields</TableHead>
                <TableHead className="text-right">Unit price</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((override) => (
                <TableRow key={override.id}>
                  <TableCell>
                    <div className="text-sm font-medium">{override.supplier_name}</div>
                    <div className="font-mono text-xs text-muted-foreground">
                      {override.article_number}
                    </div>
                    <div className="max-w-80 truncate text-xs text-muted-foreground">
                      {override.description}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex max-w-80 flex-wrap gap-1">
                      {override.manual_overrides.map((field) => (
                        <Badge key={field} variant="secondary" className="font-mono text-[10px]">
                          {field}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right">
                    {override.unit_price_net !== null
                      ? formatCurrency(override.unit_price_net)
                      : '—'}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(override.updated_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => openEdit(override)}
                    >
                      Manage
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {total === 0 ? 'No results' : `Showing ${rangeStart}–${rangeEnd} of ${total}`}
        </p>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page === 0 || overrides.isFetching}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
          >
            <ChevronLeft />
            Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {page + 1} of {pageCount}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page + 1 >= pageCount || overrides.isFetching}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
            <ChevronRight />
          </Button>
        </div>
      </div>

      <OverrideDialog
        key={dialogOpen ? editing?.id ?? 'new' : 'closed'}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editing}
      />
    </div>
  )
}
