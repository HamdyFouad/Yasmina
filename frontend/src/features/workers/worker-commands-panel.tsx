import * as React from 'react'
import { Ban, RefreshCw } from 'lucide-react'

import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { TablePagination } from '@/components/table-pagination'
import { formatDateTime } from '@/lib/utils'
import type { WorkerCommand } from '@/types/domain'

export function WorkerCommandsPanel({
  commands,
  total,
  page,
  pageSize,
  isFetching,
  onPageChange,
  onRefresh,
  onCancel,
  actionsDisabled,
}: {
  commands: WorkerCommand[]
  total: number
  page: number
  pageSize: number
  isFetching: boolean
  onPageChange: (page: number) => void
  onRefresh: () => void
  onCancel: (command: WorkerCommand) => void
  actionsDisabled: boolean
}) {
  const captionId = React.useId()

  return (
    <section aria-labelledby="worker-commands-heading" className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="worker-commands-heading" className="text-lg font-semibold tracking-tight">
            Worker commands
          </h2>
          <p className="text-sm text-muted-foreground">
            Durable admin-triggered scrape and ingestion command lifecycle, including process
            IDs and cancellation.
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={onRefresh}>
          <RefreshCw className={isFetching ? 'animate-spin' : undefined} aria-hidden="true" />
          Refresh commands
        </Button>
      </div>

      {commands.length === 0 ? (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            No worker commands have been queued yet. Use the manual actions to start one.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            aria-busy={isFetching}
            tabIndex={0}
            className="stacked-table max-h-[45vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <table className="w-full min-w-[900px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                Durable worker commands with type, target, status, process id and cancellation
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  {['Created', 'Type', 'Target', 'Status', 'PID', 'Requested by', 'Error'].map(
                    (label) => (
                      <th
                        key={label}
                        scope="col"
                        className="h-10 px-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                      >
                        {label}
                      </th>
                    ),
                  )}
                  <th
                    scope="col"
                    className="h-10 px-3 text-right text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {commands.map((command) => {
                  const target =
                    command.command_type === 'scrape'
                      ? `${command.portal ?? 'all'} · ${command.max_pages ?? 1} page(s)`
                      : `${command.batch_limit ?? 10} job(s)`
                  const active = command.status === 'queued' || command.status === 'running'
                  return (
                    <tr
                      key={command.id}
                      className="border-b border-border last:border-0 hover:bg-muted/50"
                    >
                      <th scope="row" className="whitespace-nowrap px-3 py-3 text-left align-top">
                        <time dateTime={command.created_at}>
                          {formatDateTime(command.created_at)}
                        </time>
                      </th>
                      <td data-label="Type" className="px-3 py-3 align-top capitalize">
                        {command.command_type}
                      </td>
                      <td data-label="Target" className="px-3 py-3 align-top">
                        {target}
                      </td>
                      <td data-label="Status" className="px-3 py-3 align-top">
                        <StatusBadge status={command.status} />
                      </td>
                      <td data-label="PID" className="whitespace-nowrap px-3 py-3 align-top">
                        {command.pid ?? '—'}
                      </td>
                      <td data-label="Requested by" className="px-3 py-3 align-top">
                        {command.requested_by}
                      </td>
                      <td data-label="Error" className="max-w-72 px-3 py-3 align-top text-xs">
                        <span className="line-clamp-2">{command.error ?? '—'}</span>
                      </td>
                      <td
                        data-label="Actions"
                        className="whitespace-nowrap px-3 py-3 text-right align-top"
                      >
                        {active ? (
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={actionsDisabled}
                            aria-label={`Cancel worker command ${command.command_type} ${command.id}`}
                            onClick={() => onCancel(command)}
                          >
                            <Ban />
                            Cancel run
                          </Button>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <TablePagination
        label="worker commands"
        total={total}
        page={page}
        pageSize={pageSize}
        shown={commands.length}
        isFetching={isFetching}
        onPageChange={onPageChange}
      />
    </section>
  )
}
