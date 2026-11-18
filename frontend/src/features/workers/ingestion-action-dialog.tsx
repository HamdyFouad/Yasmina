import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, RotateCcw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { apiErrorMessage } from '@/lib/api-error'
import { discardIngestionJob, retryIngestionJob } from '@/lib/ops-api'
import type { IngestionJob } from '@/types/domain'

export function IngestionActionDialog({
  job,
  action,
  onClose,
  onCompleted,
}: {
  job: IngestionJob
  action: 'retry' | 'discard'
  onClose: () => void
  onCompleted: () => void
}) {
  const queryClient = useQueryClient()
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  const [reason, setReason] = React.useState('discarded by operator')
  const [attempted, setAttempted] = React.useState(false)

  const mutation = useMutation({
    mutationFn: () =>
      action === 'retry'
        ? retryIngestionJob(job.id)
        : discardIngestionJob(job.id, reason.trim()),
    onSuccess: async () => {
      toast.success(action === 'retry' ? 'Ingestion job re-queued' : 'Ingestion job discarded', {
        description: job.tender_number ?? job.tender_id,
      })
      onCompleted()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ops', 'ingestion'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'worker-runs'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'summary'] }),
      ])
    },
  })

  const reasonError =
    action === 'discard' && reason.trim().length < 3
      ? 'Give a reason with at least 3 characters.'
      : null

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (mutation.isPending) {
      return
    }
    if (reasonError !== null) {
      return
    }
    mutation.mutate()
  }

  const label = job.quarantined ? 'malware-quarantined job' : 'dead-letter job'

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          cancelRef.current?.focus()
        }}
      >
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>
              {action === 'retry' ? 'Retry' : 'Discard'} {label}?
            </DialogTitle>
            <DialogDescription>
              {job.tender_number ?? job.tender_id} · {job.portal_name}.{' '}
              {action === 'retry'
                ? 'The job returns to pending with zero attempts and is picked up by the ingestion worker.'
                : 'The job moves to skipped and will not be processed again.'}
            </DialogDescription>
          </DialogHeader>

          <div className="rounded-md border border-border bg-muted/40 p-3 text-xs">
            <p className="font-medium">Last error</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
              {job.last_error ?? 'No error recorded.'}
            </p>
          </div>

          {action === 'retry' && job.quarantined ? (
            <p
              role="alert"
              className="rounded-md border border-warning/40 bg-warning/5 p-2 text-xs text-warning"
            >
              This job was quarantined by malware scanning. Retrying downloads and scans the
              attachment again.
            </p>
          ) : null}

          {action === 'discard' ? (
            <div className="space-y-1">
              <Label htmlFor="discard-reason">Discard reason</Label>
              <p id="discard-reason-hint" className="text-xs text-muted-foreground">
                Stored in the GoBD audit trail for this operational action.
              </p>
              <Textarea
                id="discard-reason"
                name="reason"
                rows={3}
                maxLength={512}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                aria-invalid={attempted && reasonError !== null ? true : undefined}
                aria-describedby={
                  attempted && reasonError !== null
                    ? 'discard-reason-hint discard-reason-error'
                    : 'discard-reason-hint'
                }
                disabled={mutation.isPending}
              />
              {attempted && reasonError !== null ? (
                <p id="discard-reason-error" role="alert" className="text-xs text-destructive">
                  {reasonError}
                </p>
              ) : null}
            </div>
          ) : null}

          {mutation.isError ? (
            <p role="alert" className="text-xs text-destructive">
              {apiErrorMessage(mutation.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              ref={cancelRef}
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant={action === 'discard' ? 'destructive' : 'default'}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? (
                <Loader2 className="animate-spin" />
              ) : action === 'retry' ? (
                <RotateCcw />
              ) : (
                <Trash2 />
              )}
              {action === 'retry' ? 'Retry job' : 'Discard job'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
