import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, AlertTriangle, Bell, Info, RefreshCw } from 'lucide-react'
import { Link } from 'react-router-dom'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import { fetchNotifications } from '@/lib/notifications-api'
import { cn } from '@/lib/utils'
import type { NotificationAlert } from '@/types/domain'

const SEVERITY_STYLES: Record<NotificationAlert['severity'], string> = {
  critical: 'border-destructive/30 bg-destructive/5 text-destructive',
  warning: 'border-warning/30 bg-warning/5 text-warning',
  info: 'border-border bg-muted/30 text-muted-foreground',
}

function AlertIcon({ severity }: { severity: NotificationAlert['severity'] }) {
  if (severity === 'critical') return <AlertTriangle className="h-4 w-4" aria-hidden="true" />
  if (severity === 'warning') return <AlertCircle className="h-4 w-4" aria-hidden="true" />
  return <Info className="h-4 w-4" aria-hidden="true" />
}

export function NotificationCenter() {
  const { hasRole } = useAuth()
  const visible = hasRole('admin', 'approver', 'viewer')
  const [open, setOpen] = React.useState(false)
  const notifications = useQuery({
    queryKey: ['ops', 'notifications'],
    queryFn: fetchNotifications,
    enabled: visible,
    refetchInterval: 60_000,
  })
  if (!visible) return null

  const count = notifications.data?.count ?? 0
  const alerts = notifications.data?.alerts ?? []
  const label = count > 0 ? `Notifications (${count})` : 'Notifications'

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="ghost" size="icon" className="relative" aria-label={label}>
          <Bell className="h-4 w-4" aria-hidden="true" />
          {count > 0 ? (
            <span
              className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-destructive-foreground"
              aria-hidden="true"
            >
              {count > 9 ? '9+' : count}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[22rem] p-0">
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
          <div>
            <p className="text-sm font-semibold">Notifications</p>
            <p className="text-[11px] text-muted-foreground">Critical operational alerts</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Refresh notifications"
            disabled={notifications.isFetching}
            onClick={() => void notifications.refetch()}
          >
            <RefreshCw className={cn('h-3.5 w-3.5', notifications.isFetching && 'animate-spin')} />
          </Button>
        </div>

        <p className="sr-only" role="status" aria-live="polite">
          {count === 0 ? 'No notifications' : `${count} notification${count === 1 ? '' : 's'}`}
        </p>

        <div className="max-h-[24rem] overflow-y-auto p-2">
          {notifications.isLoading ? (
            <p className="p-3 text-xs text-muted-foreground">Checking operational alerts…</p>
          ) : notifications.isError ? (
            <p role="alert" className="p-3 text-xs text-destructive">
              {apiErrorMessage(notifications.error)}
            </p>
          ) : alerts.length === 0 ? (
            <div className="flex items-start gap-2 p-3 text-xs text-muted-foreground">
              <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <p>No critical alerts. Procurement, ingestion and billing are clear.</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {alerts.map((alert) => (
                <li key={alert.id}>
                  <Link
                    to={alert.href}
                    onClick={() => setOpen(false)}
                    className={cn(
                      'flex items-start gap-2 rounded-md border p-2.5 text-xs transition-colors hover:bg-accent/60',
                      SEVERITY_STYLES[alert.severity],
                    )}
                  >
                    <AlertIcon severity={alert.severity} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="font-medium">{alert.title}</span>
                        <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                          {alert.count}
                        </Badge>
                      </span>
                      <span className="mt-0.5 block text-muted-foreground">{alert.message}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
