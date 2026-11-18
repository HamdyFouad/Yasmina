import * as React from 'react'
import { Loader2, PowerOff } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { apiErrorMessage } from '@/lib/api-error'

export function DeactivateDialog({
  title,
  description,
  isPending,
  error,
  onConfirm,
  onClose,
}: {
  title: string
  description: string
  isPending: boolean
  error: unknown
  onConfirm: () => void
  onClose: () => void
}) {
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          cancelRef.current?.focus()
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error !== null && error !== undefined ? (
          <p role="alert" className="text-xs text-destructive">
            {apiErrorMessage(error)}
          </p>
        ) : null}
        <DialogFooter>
          <Button
            ref={cancelRef}
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={isPending}>
            {isPending ? <Loader2 className="animate-spin" /> : <PowerOff />}
            Deactivate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
