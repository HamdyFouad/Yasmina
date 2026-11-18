import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Play, Unlock } from 'lucide-react'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { apiErrorMessage } from '@/lib/api-error'
import { releaseStaleLocks, triggerIngestionRun, triggerScrapeRun } from '@/lib/ops-api'
import type { PortalTargetSummary } from '@/types/domain'

export type WorkerActionMode = 'scrape' | 'ingestion' | 'release'

export function ManualWorkerDialog({
  mode,
  portals,
  onClose,
}: {
  mode: WorkerActionMode
  portals: PortalTargetSummary[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  const portalRef = React.useRef<HTMLButtonElement>(null)
  const limitRef = React.useRef<HTMLInputElement>(null)
  const [portal, setPortal] = React.useState('all')
  const [maxPages, setMaxPages] = React.useState('1')
  const [limit, setLimit] = React.useState('10')
  const [staleMinutes, setStaleMinutes] = React.useState('15')
  const [error, setError] = React.useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: async () => {
      if (mode === 'scrape') {
        return triggerScrapeRun(portal, Number.parseInt(maxPages, 10))
      }
      if (mode === 'ingestion') {
        return triggerIngestionRun(Number.parseInt(limit, 10))
      }
      return releaseStaleLocks(Number.parseInt(staleMinutes, 10) * 60)
    },
    onSuccess: async (result) => {
      if (mode === 'release') {
        const released = 'released' in result ? result.released : 0
        toast.success('Stale locks released', {
          description: `${released} job(s) returned to pending.`,
        })
      } else {
        const action = 'action' in result ? result.action : mode
        const pid = 'pid' in result ? result.pid : 0
        toast.success(action === 'scrape' ? 'Scrape run started' : 'Ingestion run started', {
          description: `Worker process ${pid} started. History refreshes automatically.`,
        })
      }
      onClose()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ops', 'worker-runs'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'worker-commands'] }),
        queryClient.invalidateQueries({ queryKey: ['ops', 'ingestion'] }),
      ])
    },
    onError: (mutationError) => {
      setError(apiErrorMessage(mutationError))
    },
  })

  const validate = (): string | null => {
    if (mode === 'scrape') {
      if (portal.length === 0) {
        portalRef.current?.focus()
        return 'Choose a portal or all targets.'
      }
      const pages = Number.parseInt(maxPages, 10)
      if (!Number.isInteger(pages) || pages < 1 || pages > 5) {
        return 'Pages must be a whole number between 1 and 5.'
      }
    }
    if (mode === 'ingestion') {
      const parsed = Number.parseInt(limit, 10)
      if (!Number.isInteger(parsed) || parsed < 1 || parsed > 50) {
        limitRef.current?.focus()
        return 'Batch size must be between 1 and 50.'
      }
    }
    if (mode === 'release') {
      const minutes = Number.parseInt(staleMinutes, 10)
      if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
        return 'Stale age must be between 1 and 1440 minutes.'
      }
    }
    return null
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (mutation.isPending) {
      return
    }
    const validationError = validate()
    setError(validationError)
    if (validationError === null) {
      mutation.mutate()
    }
  }

  const titles: Record<WorkerActionMode, string> = {
    scrape: 'Trigger scrape run',
    ingestion: 'Process ingestion queue',
    release: 'Release stale locks?',
  }
  const descriptions: Record<WorkerActionMode, string> = {
    scrape:
      'Starts a detached one-shot scrape worker. Portal advisory locks still prevent overlapping runs.',
    ingestion:
      'Starts a detached ingestion worker for a bounded batch. The queue and worker history refresh after completion.',
    release:
      'Resets ingestion jobs stuck in processing beyond the threshold. Use this only after confirming the worker is no longer alive.',
  }
  const submitLabels: Record<WorkerActionMode, string> = {
    scrape: 'Start scrape',
    ingestion: 'Start ingestion',
    release: 'Release locks',
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          if (mode === 'scrape') {
            portalRef.current?.focus()
          } else if (mode === 'ingestion') {
            limitRef.current?.focus()
          } else {
            cancelRef.current?.focus()
          }
        }}
      >
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{titles[mode]}</DialogTitle>
            <DialogDescription>{descriptions[mode]}</DialogDescription>
          </DialogHeader>

          {mode === 'scrape' ? (
            <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
              <div className="space-y-1">
                <Label htmlFor="trigger-portal">Portal</Label>
                <Select value={portal} onValueChange={setPortal} disabled={mutation.isPending}>
                  <SelectTrigger id="trigger-portal" ref={portalRef}>
                    <SelectValue placeholder="All targets" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All enabled targets</SelectItem>
                    {portals
                      .filter((target) => target.enabled)
                      .map((target) => (
                        <SelectItem key={target.id} value={target.id}>
                          {target.name} ({target.id})
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="trigger-pages">Pages</Label>
                <Input
                  id="trigger-pages"
                  inputMode="numeric"
                  value={maxPages}
                  onChange={(event) => setMaxPages(event.target.value)}
                  disabled={mutation.isPending}
                />
              </div>
            </div>
          ) : null}

          {mode === 'ingestion' ? (
            <div className="space-y-1">
              <Label htmlFor="trigger-limit">Batch size</Label>
              <Input
                id="trigger-limit"
                ref={limitRef}
                inputMode="numeric"
                value={limit}
                onChange={(event) => setLimit(event.target.value)}
                disabled={mutation.isPending}
              />
              <p className="text-xs text-muted-foreground">
                Claim at most this many queued jobs; 1–50.
              </p>
            </div>
          ) : null}

          {mode === 'release' ? (
            <div className="space-y-1">
              <Label htmlFor="trigger-stale">Older than (minutes)</Label>
              <Input
                id="trigger-stale"
                inputMode="numeric"
                value={staleMinutes}
                onChange={(event) => setStaleMinutes(event.target.value)}
                aria-describedby="trigger-stale-hint"
                disabled={mutation.isPending}
              />
              <p id="trigger-stale-hint" className="text-xs text-muted-foreground">
                Jobs locked longer than this return to pending with one attempt refunded.
              </p>
            </div>
          ) : null}

          {error !== null ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          {mutation.isError && error === null ? (
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
              variant={mode === 'release' ? 'destructive' : 'default'}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : mode === 'release' ? (
                <Unlock aria-hidden="true" />
              ) : (
                <Play aria-hidden="true" />
              )}
              {submitLabels[mode]}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
