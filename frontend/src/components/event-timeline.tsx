import * as React from 'react'
import {
  Activity,
  Ban,
  CreditCard,
  FileText,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { cn, formatCurrency, formatDateTime, titleCase } from '@/lib/utils'
import type { Currency } from '@/types/domain'

export interface EventTimelineItem {
  id: string
  sequence: number
  event_type: string
  occurred_at: string
  actor: string | null
  amount?: string | null
  reference?: string | null
  prev_hash?: string
  entry_hash?: string
  payload?: Record<string, unknown>
}

function iconFor(eventType: string): LucideIcon {
  const normalized = eventType.toUpperCase()
  if (normalized.includes('PAYMENT')) {
    return CreditCard
  }
  if (normalized.includes('CREDIT')) {
    return RotateCcw
  }
  if (normalized.includes('STATUS')) {
    return RefreshCw
  }
  if (normalized.includes('CANCEL')) {
    return Ban
  }
  if (normalized.includes('ISSUED')) {
    return FileText
  }
  return Activity
}

export function EventTimeline({
  events,
  chainValid,
  currency = 'EUR',
  title = 'GoBD event log',
  description = 'Hash-chained lifecycle events, oldest first. Any edited entry breaks verification.',
  emptyMessage = 'No audit events have been recorded for this entity yet.',
  className,
}: {
  events: EventTimelineItem[]
  chainValid: boolean | null
  currency?: Currency
  title?: string
  description?: string
  emptyMessage?: string
  className?: string
}) {
  const headingId = React.useId()

  return (
    <section aria-labelledby={headingId} className={cn('space-y-3', className)}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={headingId} className="text-sm font-semibold tracking-tight">
            {title}
          </h3>
          <p className="max-w-prose text-xs text-muted-foreground">{description}</p>
        </div>
        {chainValid === null ? (
          <Badge variant="secondary">Not verified</Badge>
        ) : chainValid ? (
          <Badge variant="success">
            <ShieldCheck className="mr-1 h-3 w-3" aria-hidden="true" />
            Chain verified
          </Badge>
        ) : (
          <Badge variant="destructive">
            <ShieldAlert className="mr-1 h-3 w-3" aria-hidden="true" />
            Chain broken
          </Badge>
        )}
      </div>

      {events.length === 0 ? (
        <p className="rounded-md border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
          {emptyMessage}
        </p>
      ) : (
        <ol className="space-y-4">
          {events.map((event) => {
            const Icon = iconFor(event.event_type)
            const hasPayload =
              event.payload !== undefined && Object.keys(event.payload).length > 0
            const hasHashes =
              event.prev_hash !== undefined && event.entry_hash !== undefined
            return (
              <li key={event.id} className="relative border-l border-border pl-5">
                <span className="absolute -left-[0.6875rem] top-0.5 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-card text-muted-foreground">
                  <Icon className="h-3 w-3" aria-hidden="true" />
                </span>
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="text-sm font-medium">{titleCase(event.event_type)}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    #{event.sequence}
                  </span>
                  <time
                    dateTime={event.occurred_at}
                    className="text-xs text-muted-foreground"
                  >
                    {formatDateTime(event.occurred_at)}
                  </time>
                </div>
                <p className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {event.actor ?? 'System'}
                  </span>
                  {event.amount !== undefined && event.amount !== null ? (
                    <> · {formatCurrency(event.amount, currency)}</>
                  ) : null}
                  {event.reference !== undefined && event.reference !== null ? (
                    <> · {event.reference}</>
                  ) : null}
                </p>

                {hasHashes ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-muted-foreground">
                      Hash chain and payload
                    </summary>
                    <dl className="mt-2 space-y-1 rounded-md border border-border bg-muted/40 p-2 font-mono text-[10px]">
                      <div>
                        <dt className="text-muted-foreground">Previous hash</dt>
                        <dd className="break-all">{event.prev_hash}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Entry hash</dt>
                        <dd className="break-all">{event.entry_hash}</dd>
                      </div>
                    </dl>
                    {hasPayload ? (
                      <pre className="mt-2 max-h-48 overflow-auto rounded-md border border-border bg-muted/40 p-2 font-mono text-[10px] whitespace-pre-wrap break-all">
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    ) : null}
                  </details>
                ) : null}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
