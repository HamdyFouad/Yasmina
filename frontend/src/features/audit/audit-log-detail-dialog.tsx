import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { formatDateTime, titleCase } from '@/lib/utils'
import type { AuditLogEntry } from '@/types/domain'

export function AuditLogDetailDialog({
  entry,
  onClose,
}: {
  entry: AuditLogEntry
  onClose: () => void
}) {
  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Audit event #{entry.id}</DialogTitle>
          <DialogDescription>
            {titleCase(entry.event_type)} · {formatDateTime(entry.occurred_at)} · {entry.actor}
          </DialogDescription>
        </DialogHeader>

        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Entity</dt>
            <dd className="mt-0.5 font-medium">
              {entry.entity_type}:{entry.entity_id}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Occurred at</dt>
            <dd className="mt-0.5">{formatDateTime(entry.occurred_at)}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Payload</dt>
            <dd>
              <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">
                {JSON.stringify(entry.payload, null, 2)}
              </pre>
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Entry hash</dt>
            <dd className="mt-0.5 break-all font-mono text-xs">{entry.entry_hash}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Previous hash</dt>
            <dd className="mt-0.5 break-all font-mono text-xs">{entry.prev_hash ?? 'genesis'}</dd>
          </div>
          {entry.signature !== null ? (
            <div className="sm:col-span-2">
              <dt className="text-xs uppercase tracking-wide text-muted-foreground">Signature</dt>
              <dd className="mt-0.5 break-all font-mono text-xs">{entry.signature}</dd>
            </div>
          ) : null}
        </dl>

        <DialogFooter>
          <Button type="button" onClick={onClose}>
            Close audit event details
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
