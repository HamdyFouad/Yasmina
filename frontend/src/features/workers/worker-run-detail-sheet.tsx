import { CircleCheck, CircleX, Loader2 } from 'lucide-react'

import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { formatDateTime } from '@/lib/utils'
import type { WorkerRun } from '@/types/domain'

function Field({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words text-sm">{value}</dd>
    </div>
  )
}

export function WorkerRunDetailSheet({
  run,
  onClose,
}: {
  run: WorkerRun
  onClose: () => void
}) {
  const duration =
    run.duration_ms === null
      ? run.status === 'running'
        ? 'In flight'
        : '—'
      : run.duration_ms < 1000
        ? `${run.duration_ms} ms`
        : `${(run.duration_ms / 1000).toFixed(1)} s`

  return (
    <Sheet open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <SheetContent className="sm:max-w-xl lg:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Worker run {run.resource}</SheetTitle>
          <SheetDescription>
            {run.run_type} run started {formatDateTime(run.started_at)} by {run.worker_id}.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={run.status} />
            {run.status === 'running' ? (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                Still running
              </span>
            ) : run.errors_count > 0 ? (
              <span className="flex items-center gap-1 text-xs text-destructive">
                <CircleX className="h-3.5 w-3.5" aria-hidden="true" />
                {run.errors_count} error(s)
              </span>
            ) : (
              <span className="flex items-center gap-1 text-xs text-success">
                <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
                No errors recorded
              </span>
            )}
          </div>

          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="Run ID" value={run.id} />
            <Field label="Type" value={run.run_type} />
            <Field label="Resource" value={run.resource} />
            <Field label="Started" value={formatDateTime(run.started_at)} />
            <Field
              label="Finished"
              value={run.finished_at !== null ? formatDateTime(run.finished_at) : '—'}
            />
            <Field label="Duration" value={duration} />
            <Field label="Items" value={run.items_processed.toLocaleString('de-DE')} />
            <Field label="Errors" value={run.errors_count.toLocaleString('de-DE')} />
            <Field label="Worker" value={run.worker_id} />
          </dl>

          <section aria-labelledby="run-error-heading" className="space-y-2">
            <h3 id="run-error-heading" className="text-sm font-medium">
              Error text
            </h3>
            {run.error !== null ? (
              <pre className="max-h-48 overflow-auto rounded-md border border-destructive/30 bg-destructive/5 p-3 whitespace-pre-wrap break-words text-xs text-destructive">
                {run.error}
              </pre>
            ) : (
              <p className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
                No error text was recorded for this run.
              </p>
            )}
          </section>

          <section aria-labelledby="run-details-heading" className="space-y-2">
            <h3 id="run-details-heading" className="text-sm font-medium">
              Details JSON
            </h3>
            <pre className="max-h-80 overflow-auto rounded-md border border-border bg-muted/30 p-3 text-xs">
              {JSON.stringify(run.details, null, 2)}
            </pre>
          </section>
        </div>

        <SheetFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Close
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
