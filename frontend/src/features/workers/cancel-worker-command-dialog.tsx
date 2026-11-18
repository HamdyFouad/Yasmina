import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Loader2 } from 'lucide-react'
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
import { cancelWorkerCommand } from '@/lib/ops-api'
import type { WorkerCommand } from '@/types/domain'

export function CancelWorkerCommandDialog({
  command,
  onClose,
  onCompleted,
}: {
  command: WorkerCommand
  onClose: () => void
  onCompleted: () => void
}) {
  const queryClient = useQueryClient()
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  const [reason, setReason] = React.useState('cancelled by operator')
  const [attempted, setAttempted] = React.useState(false)

  const mutation = useMutation({
    mutationFn: () => cancelWorkerCommand(command.id, reason.trim()),
    onSuccess: async (result) => {
      toast.success('Worker command cancelled', {
        description: result.terminated
          ? 'The worker process was signalled to stop.'
          : 'The command was marked cancelled.',
      })
      onClose()
      onCompleted()
      await queryClient.invalidateQueries({ queryKey: ['ops', 'worker-commands'] })
      await queryClient.invalidateQueries({ queryKey: ['ops', 'worker-runs'] })
    },
  })

  const reasonError =
    reason.trim().length < 3 ? 'Give a reason with at least 3 characters.' : null

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (mutation.isPending) {
      return
    }
    setAttempted(true)
    if (reasonError !== null) {
      return
    }
    mutation.mutate()
  }

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
            <DialogTitle>Cancel {command.command_type} worker run?</DialogTitle>
            <DialogDescription>
              Command {command.id.slice(0, 8)} requested by {command.requested_by}. A running
              process receives SIGTERM; a queued command is closed immediately.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1">
            <Label htmlFor="command-cancel-reason">Cancellation reason</Label>
            <p id="command-cancel-reason-hint" className="text-xs text-muted-foreground">
              Stored in the audit trail and the command error field.
            </p>
            <Textarea
              id="command-cancel-reason"
              rows={3}
              maxLength={512}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              aria-invalid={attempted && reasonError !== null ? true : undefined}
              aria-describedby={
                attempted && reasonError !== null
                  ? 'command-cancel-reason-hint command-cancel-reason-error'
                  : 'command-cancel-reason-hint'
              }
              disabled={mutation.isPending}
            />
            {attempted && reasonError !== null ? (
              <p
                id="command-cancel-reason-error"
                role="alert"
                className="text-xs text-destructive"
              >
                {reasonError}
              </p>
            ) : null}
          </div>

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
              Keep running
            </Button>
            <Button type="submit" variant="destructive" disabled={mutation.isPending}>
              {mutation.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <Ban aria-hidden="true" />
              )}
              Cancel run
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
