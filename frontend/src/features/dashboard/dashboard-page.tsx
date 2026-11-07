import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BadgeCheck,
  BarChart3,
  FileText,
  FileUp,
  LogIn,
  LogOut,
  ReceiptText,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Tags,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import { API_BASE_URL } from '@/lib/env'
import { fetchOpsActivity, fetchOpsSummary } from '@/lib/ops-api'
import { formatCurrency, formatDateTime, titleCase } from '@/lib/utils'

function KpiCard({
  title,
  value,
  hint,
  icon: Icon,
  loading,
  error,
}: {
  title: string
  value: string
  hint: string
  icon: LucideIcon
  loading: boolean
  error?: boolean
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <div className="text-2xl font-semibold tracking-tight">{value}</div>
        )}
        <div className="mt-1 flex items-center gap-2">
          <p className="text-xs text-muted-foreground">{hint}</p>
          {error === true ? (
            <Badge variant="destructive" className="px-1.5 py-0 text-[10px]">
              Unavailable
            </Badge>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function backendOrigin(): string {
  try {
    return new URL(API_BASE_URL, window.location.origin).origin
  } catch {
    return window.location.origin
  }
}

function activityIcon(eventType: string): LucideIcon {
  if (eventType.includes('LOGIN')) {
    return LogIn
  }
  if (eventType.includes('LOGOUT')) {
    return LogOut
  }
  if (eventType.startsWith('USER_')) {
    return ShieldCheck
  }
  if (eventType.includes('OFFER') || eventType.includes('APPROVAL')) {
    return BadgeCheck
  }
  if (eventType.includes('INVOICE') || eventType.includes('PAYMENT')) {
    return ReceiptText
  }
  if (eventType.includes('TENDER') || eventType.includes('INGEST')) {
    return FileUp
  }
  if (eventType.startsWith('SYSTEM_')) {
    return Settings2
  }
  return Activity
}

export function DashboardPage() {
  const { user, roles, hasRole } = useAuth()
  const canSeeSummary = hasRole('admin', 'viewer', 'sales')
  const canSeeActivity = hasRole('admin', 'viewer')

  const summary = useQuery({
    queryKey: ['ops', 'summary'],
    queryFn: fetchOpsSummary,
    enabled: canSeeSummary,
  })
  const activity = useQuery({
    queryKey: ['ops', 'activity'],
    queryFn: () => fetchOpsActivity(20),
    enabled: canSeeActivity,
  })

  const summaryData = summary.data
  const loading = summary.isLoading
  const number = (value: number | undefined): string =>
    value !== undefined ? value.toLocaleString('de-DE') : '—'

  const activityItems = activity.data?.events ?? []

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Day 2 operations cockpit for tenders, catalogue matching, pricing and compliant invoices."
        actions={
          <Badge variant="outline" className="gap-1.5 border-border bg-card">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            Live session
          </Badge>
        }
      />

      {!canSeeSummary ? (
        <Card>
          <CardContent className="flex items-start gap-3 p-5 text-sm">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warning" />
            <div>
              <p className="font-medium">No dashboard aggregates for this role</p>
              <p className="text-muted-foreground">
                KPI aggregates are available to sales, viewer and admin accounts. Your roles:{' '}
                {roles.map(titleCase).join(', ') || 'none'}.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          title="Tenders"
          value={number(summaryData?.tenders)}
          hint={canSeeSummary ? 'Visible to your role' : 'Requires a dashboard role'}
          icon={FileText}
          loading={loading}
          error={summary.isError}
        />
        <KpiCard
          title="Offers"
          value={number(summaryData?.offers)}
          hint="Draft, pending and approved offers"
          icon={Tags}
          loading={loading}
          error={summary.isError}
        />
        <KpiCard
          title="Pending approvals"
          value={number(summaryData?.pending_approvals)}
          hint="Offers waiting for four-eyes approval"
          icon={BadgeCheck}
          loading={loading}
          error={summary.isError}
        />
        <KpiCard
          title="Invoices issued"
          value={number(summaryData?.invoices)}
          hint={
            summaryData !== undefined && summaryData.overdue_invoices > 0
              ? `${summaryData.overdue_invoices} overdue`
              : 'GoBD-compliant invoice records'
          }
          icon={ReceiptText}
          loading={loading}
          error={summary.isError}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle>Recent activity</CardTitle>
            {activity.isFetching && canSeeActivity ? (
              <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Activity className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            )}
          </CardHeader>
          <CardContent>
            {!canSeeActivity ? (
              <EmptyState
                icon={BarChart3}
                title="Audit stream is restricted"
                description="The global audit and system event feed is available to admin and viewer accounts."
                className="border-0 bg-transparent py-8 shadow-none"
              />
            ) : activity.isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }, (_, index) => (
                  <Skeleton key={index} className="h-12 w-full" />
                ))}
              </div>
            ) : activity.isError ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <AlertTriangle className="h-5 w-5 text-destructive" />
                <p className="text-sm text-muted-foreground">{apiErrorMessage(activity.error)}</p>
                <Button variant="outline" size="sm" onClick={() => void activity.refetch()}>
                  <RefreshCw />
                  Retry
                </Button>
              </div>
            ) : activityItems.length === 0 ? (
              <EmptyState
                icon={Activity}
                title="No audit events yet"
                description="Login, workflow and invoice events will appear here as operators use the platform."
                className="border-0 bg-transparent py-8 shadow-none"
              />
            ) : (
              <ul className="divide-y divide-border">
                {activityItems.map((event) => {
                  const Icon = activityIcon(event.event_type)
                  return (
                    <li key={event.id} className="flex items-start gap-3 py-2.5">
                      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                        <Icon className="h-3.5 w-3.5" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <span className="text-sm font-medium">{titleCase(event.event_type)}</span>
                          <span className="text-[11px] text-muted-foreground">
                            {formatDateTime(event.occurred_at)}
                          </span>
                        </div>
                        <div className="truncate text-xs text-muted-foreground">
                          {event.actor} · {event.entity_type}:{event.entity_id}
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Billing</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {summary.isLoading ? (
                <>
                  <Skeleton className="h-5 w-full" />
                  <Skeleton className="h-5 w-full" />
                  <Skeleton className="h-5 w-full" />
                </>
              ) : summaryData !== undefined ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Gross invoiced</span>
                    <span className="font-medium">
                      {formatCurrency(summaryData.gross_invoiced, summaryData.currency)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Gross paid</span>
                    <span className="font-medium">
                      {formatCurrency(summaryData.gross_paid, summaryData.currency)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between border-t border-border pt-3">
                    <span className="text-muted-foreground">Outstanding</span>
                    <span className="font-semibold">
                      {formatCurrency(
                        Math.max(
                          0,
                          Number.parseFloat(summaryData.gross_invoiced) -
                            Number.parseFloat(summaryData.gross_paid),
                        ).toFixed(2),
                        summaryData.currency,
                      )}
                    </span>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Billing aggregates are unavailable for this session.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Session</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">Subject</div>
                <div className="mt-0.5 truncate font-medium">{user?.subject ?? '—'}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">Roles</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {roles.map((role) => (
                    <Badge key={role} variant="secondary">
                      {titleCase(role)}
                    </Badge>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">MFA</div>
                <div className="mt-0.5 font-medium">
                  {user?.mfa_enabled === true
                    ? 'Enabled'
                    : user?.mfa_enabled === false
                      ? 'Not enrolled'
                      : 'Bootstrap admin'}
                </div>
              </div>
              <div className="space-y-2 pt-2">
                <Button asChild variant="outline" size="sm" className="w-full justify-between">
                  <a href={`${backendOrigin()}/docs`} target="_blank" rel="noreferrer">
                    OpenAPI / Swagger
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </a>
                </Button>
                <Button asChild variant="outline" size="sm" className="w-full justify-between">
                  <a href={`${backendOrigin()}/metrics`} target="_blank" rel="noreferrer">
                    Prometheus metrics
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </a>
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
