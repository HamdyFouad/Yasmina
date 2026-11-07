import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  FileText,
  Loader2,
  RefreshCw,
} from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
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
import { TenderDetailSheet } from '@/features/tenders/tender-detail-sheet'
import { apiErrorMessage } from '@/lib/api-error'
import { fetchTenders } from '@/lib/tenders-api'
import { formatDate } from '@/lib/utils'
import type { TenderStatus } from '@/types/domain'

const PAGE_SIZE = 10

const STATUS_OPTIONS: { value: TenderStatus | 'ALL'; label: string }[] = [
  { value: 'ALL', label: 'All statuses' },
  { value: 'DRAFT', label: 'Draft' },
  { value: 'PUBLISHED', label: 'Published' },
  { value: 'CLOSED', label: 'Closed' },
  { value: 'IN_EVALUATION', label: 'In evaluation' },
  { value: 'AWARDED', label: 'Awarded' },
  { value: 'CANCELLED', label: 'Cancelled' },
  { value: 'ARCHIVED', label: 'Archived' },
]

function isTenderStatus(value: string): value is TenderStatus {
  return STATUS_OPTIONS.some((option) => option.value === value && option.value !== 'ALL')
}

function isPastDeadline(value: string): boolean {
  const deadline = new Date(value)
  return !Number.isNaN(deadline.getTime()) && deadline.getTime() < Date.now()
}

export function TendersPage() {
  const [status, setStatus] = React.useState<TenderStatus | null>(null)
  const [page, setPage] = React.useState(0)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)

  const offset = page * PAGE_SIZE
  const tenders = useQuery({
    queryKey: ['tenders', { status, offset }],
    queryFn: () =>
      fetchTenders({
        limit: PAGE_SIZE,
        offset,
        ...(status !== null ? { status } : {}),
      }),
  })

  const total = tenders.data?.count ?? 0
  const rows = tenders.data?.tenders ?? []
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = offset + rows.length

  const handleStatusChange = (value: string): void => {
    setStatus(isTenderStatus(value) ? value : null)
    setPage(0)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tenders & pipeline"
        description="Discovered and published tenders, their deadlines, CPV codes and attached documents."
        actions={
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={tenders.isFetching}
            onClick={() => void tenders.refetch()}
          >
            {tenders.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Refresh
          </Button>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="tender-status" className="text-sm text-muted-foreground">
            Status
          </label>
          <Select
            value={status ?? 'ALL'}
            onValueChange={handleStatusChange}
          >
            <SelectTrigger id="tender-status" className="w-44">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <p className="text-xs text-muted-foreground">
          {total.toLocaleString('de-DE')} tender(s) visible to your role
        </p>
      </div>

      {tenders.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : tenders.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <AlertTriangle className="h-6 w-6 text-destructive" />
            <div>
              <p className="text-sm font-medium">Could not load tenders</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(tenders.error)}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void tenders.refetch()}>
              <RefreshCw />
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No tenders found"
          description={
            status === null
              ? 'No tenders have been ingested yet. Upload a GAEB or PDF tender to populate the board.'
              : 'No tenders match the selected status filter.'
          }
          hint="POST /tenders/upload accepts .x83, .d83 and .pdf documents."
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tender</TableHead>
                <TableHead>Client</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Deadline</TableHead>
                <TableHead className="text-right">Positions</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((tender) => (
                <TableRow
                  key={tender.id}
                  className="cursor-pointer"
                  onClick={() => setSelectedId(tender.id)}
                >
                  <TableCell>
                    <div className="font-mono text-xs text-muted-foreground">
                      {tender.tender_number}
                    </div>
                    <div className="max-w-80 truncate font-medium">{tender.title}</div>
                  </TableCell>
                  <TableCell className="max-w-48 truncate">{tender.client_name}</TableCell>
                  <TableCell>
                    <StatusBadge status={tender.status} />
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    <div>{formatDate(tender.submission_deadline)}</div>
                    {isPastDeadline(tender.submission_deadline) ? (
                      <div className="text-[11px] font-medium text-destructive">Deadline passed</div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right">
                    {tender.items.length.toLocaleString('de-DE')}
                  </TableCell>
                  <TableCell className="max-w-32 truncate text-sm text-muted-foreground">
                    {tender.owner ?? '—'}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={(event) => {
                        event.stopPropagation()
                        setSelectedId(tender.id)
                      }}
                    >
                      Open
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
            disabled={page === 0 || tenders.isFetching}
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
            disabled={page + 1 >= pageCount || tenders.isFetching}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
            <ChevronRight />
          </Button>
        </div>
      </div>

      <TenderDetailSheet
        tenderId={selectedId}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedId(null)
          }
        }}
      />
    </div>
  )
}
