import * as React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Eye, FileSpreadsheet, FileText, RefreshCw, ScrollText, Search } from 'lucide-react'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { TablePagination } from '@/components/table-pagination'
import { Badge } from '@/components/ui/badge'
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
import { AuditLogDetailDialog } from '@/features/audit/audit-log-detail-dialog'
import { apiErrorMessage } from '@/lib/api-error'
import {
  downloadAuditExport,
  fetchAuditEventTypes,
  fetchAuditLogs,
  triggerDownload,
  type AuditFilters,
} from '@/lib/audit-api'
import { formatDateTime, titleCase } from '@/lib/utils'
import type { AuditExportFormat, AuditLogEntry } from '@/types/domain'

const PAGE_SIZE = 50

export function AuditPage() {
  const [eventType, setEventType] = React.useState('ALL')
  const [actor, setActor] = React.useState('')
  const [entityType, setEntityType] = React.useState('')
  const [search, setSearch] = React.useState('')
  const [dateFrom, setDateFrom] = React.useState('')
  const [dateTo, setDateTo] = React.useState('')
  const [page, setPage] = React.useState(0)
  const [selected, setSelected] = React.useState<AuditLogEntry | null>(null)
  const deferredActor = React.useDeferredValue(actor)
  const deferredEntity = React.useDeferredValue(entityType)
  const deferredSearch = React.useDeferredValue(search)
  const captionId = React.useId()

  const filters: AuditFilters = {
    ...(eventType !== 'ALL' ? { eventType } : {}),
    actor: deferredActor,
    entityType: deferredEntity,
    search: deferredSearch,
    ...(dateFrom ? { dateFrom } : {}),
    ...(dateTo ? { dateTo } : {}),
  }

  const eventTypes = useQuery({
    queryKey: ['audit', 'event-types'],
    queryFn: fetchAuditEventTypes,
    staleTime: 300_000,
  })
  const logs = useQuery({
    queryKey: ['audit', 'logs', { eventType, deferredActor, deferredEntity, deferredSearch, dateFrom, dateTo, page }],
    queryFn: () =>
      fetchAuditLogs(filters, {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    placeholderData: (previous) => previous,
  })

  const exportMutation = useMutation({
    mutationFn: (format: AuditExportFormat) => downloadAuditExport(format, filters),
    onSuccess: ({ blob, filename }) => {
      triggerDownload(blob, filename)
      toast.success(`Audit ${filename.endsWith('.xlsx') ? 'XLSX' : 'CSV'} export started`, {
        description: filename,
      })
    },
    onError: (error) => {
      toast.error(apiErrorMessage(error))
    },
  })

  const rows = logs.data?.logs ?? []
  const total = logs.data?.count ?? 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit Logs"
        description="Admin-only view of the append-only, hash-chained GoBD audit trail."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={exportMutation.isPending}
              onClick={() => exportMutation.mutate('csv')}
            >
              <FileText />
              Export CSV
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={exportMutation.isPending}
              onClick={() => exportMutation.mutate('xlsx')}
            >
              <FileSpreadsheet />
              Export XLSX
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={logs.isFetching}
              onClick={() => void logs.refetch()}
            >
              <RefreshCw className={logs.isFetching ? 'animate-spin' : undefined} />
              Refresh
            </Button>
          </div>
        }
      />

      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4">
          <div className="space-y-1">
            <Label htmlFor="audit-event-type">Event type</Label>
            <Select
              value={eventType}
              onValueChange={(value) => {
                setEventType(value)
                setPage(0)
              }}
            >
              <SelectTrigger id="audit-event-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All event types</SelectItem>
                {(eventTypes.data?.event_types ?? []).map((value) => (
                  <SelectItem key={value} value={value}>
                    {titleCase(value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-actor">Actor</Label>
            <Input
              id="audit-actor"
              value={actor}
              maxLength={128}
              placeholder="Username substring"
              onChange={(event) => {
                setActor(event.target.value)
                setPage(0)
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-entity-type">Entity type</Label>
            <Input
              id="audit-entity-type"
              value={entityType}
              maxLength={64}
              placeholder="invoice, tender, user…"
              onChange={(event) => {
                setEntityType(event.target.value)
                setPage(0)
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-search">Search</Label>
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                id="audit-search"
                value={search}
                maxLength={128}
                placeholder="Actor, entity or payload"
                className="pl-8"
                onChange={(event) => {
                  setSearch(event.target.value)
                  setPage(0)
                }}
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-date-from">From</Label>
            <Input
              id="audit-date-from"
              type="date"
              value={dateFrom}
              onChange={(event) => {
                setDateFrom(event.target.value)
                setPage(0)
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="audit-date-to">To</Label>
            <Input
              id="audit-date-to"
              type="date"
              value={dateTo}
              onChange={(event) => {
                setDateTo(event.target.value)
                setPage(0)
              }}
            />
          </div>
        </CardContent>
      </Card>

      {logs.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : logs.isError ? (
        <Card>
          <CardContent role="alert" className="p-5 text-sm text-destructive">
            {apiErrorMessage(logs.error)}
          </CardContent>
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={ScrollText}
          title="No audit events match"
          description="Adjust the filters or clear the date range."
        />
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            aria-busy={logs.isFetching}
            tabIndex={0}
            className="stacked-table max-h-[65vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <table className="w-full min-w-[980px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                Audit log events with timestamp, event type, actor, entity, hash and details
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  {['Time', 'Event', 'Actor', 'Entity', 'Hash', 'Details'].map((label) => (
                    <th
                      key={label}
                      scope="col"
                      className="h-10 px-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((entry) => (
                  <tr key={entry.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                    <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left align-top">
                      <span className="block font-medium">{formatDateTime(entry.occurred_at)}</span>
                      <span className="block text-[11px] text-muted-foreground">#{entry.id}</span>
                    </th>
                    <td data-label="Event" className="px-3 py-2.5 align-top">
                      <Badge variant="secondary" className="text-[11px]">
                        {titleCase(entry.event_type)}
                      </Badge>
                    </td>
                    <td data-label="Actor" className="px-3 py-2.5 align-top">
                      {entry.actor}
                    </td>
                    <td data-label="Entity" className="px-3 py-2.5 align-top text-xs">
                      <span className="block">{entry.entity_type}</span>
                      <span className="block break-all font-mono text-muted-foreground">
                        {entry.entity_id}
                      </span>
                    </td>
                    <td data-label="Hash" className="max-w-40 px-3 py-2.5 align-top">
                      <span className="block truncate font-mono text-[11px]" title={entry.entry_hash}>
                        {entry.entry_hash.slice(0, 16)}…
                      </span>
                    </td>
                    <td data-label="Details" className="whitespace-nowrap px-3 py-2.5 align-top">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={`View audit event ${entry.id} details`}
                        onClick={() => setSelected(entry)}
                      >
                        <Eye />
                        View
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <TablePagination
        label="audit logs"
        total={total}
        page={page}
        pageSize={PAGE_SIZE}
        shown={rows.length}
        isFetching={logs.isFetching}
        onPageChange={setPage}
      />

      {selected !== null ? (
        <AuditLogDetailDialog entry={selected} onClose={() => setSelected(null)} />
      ) : null}
    </div>
  )
}
