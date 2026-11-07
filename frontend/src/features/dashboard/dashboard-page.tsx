import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ArrowUpRight,
  BadgeCheck,
  BarChart3,
  FileText,
  ReceiptText,
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
import { fetchInvoices } from '@/lib/invoices-api'
import { fetchOffers } from '@/lib/offers-api'
import { API_BASE_URL } from '@/lib/env'
import { titleCase } from '@/lib/utils'

function KpiCard({
  title,
  value,
  hint,
  icon: Icon,
  loading,
  pending,
}: {
  title: string
  value: string
  hint: string
  icon: LucideIcon
  loading: boolean
  pending?: boolean
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
          {pending === true ? (
            <Badge variant="warning" className="px-1.5 py-0 text-[10px]">
              API pending
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

export function DashboardPage() {
  const { user, roles } = useAuth()
  const invoices = useQuery({
    queryKey: ['invoices', 'summary'],
    queryFn: () => fetchInvoices({ limit: 1 }),
  })
  const pendingOffers = useQuery({
    queryKey: ['offers', 'pending-approval'],
    queryFn: () => fetchOffers({ limit: 1, status: 'PENDING_APPROVAL' }),
  })
  const offers = useQuery({
    queryKey: ['offers', 'summary'],
    queryFn: () => fetchOffers({ limit: 1 }),
  })

  const invoiceCount = invoices.data?.count
  const pendingCount = pendingOffers.data?.count
  const offerCount = offers.data?.count

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

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          title="Tenders discovered"
          value="—"
          hint="Tender read API not implemented yet"
          icon={FileText}
          loading={false}
          pending
        />
        <KpiCard
          title="Pending approvals"
          value={pendingCount !== undefined ? pendingCount.toLocaleString('de-DE') : '—'}
          hint="Offers waiting for four-eyes approval"
          icon={BadgeCheck}
          loading={pendingOffers.isLoading}
        />
        <KpiCard
          title="Invoices issued"
          value={invoiceCount !== undefined ? invoiceCount.toLocaleString('de-DE') : '—'}
          hint="GoBD-compliant invoice records"
          icon={ReceiptText}
          loading={invoices.isLoading}
        />
        <KpiCard
          title="Offers"
          value={offerCount !== undefined ? offerCount.toLocaleString('de-DE') : '—'}
          hint="All customer offers visible to your role"
          icon={Tags}
          loading={offers.isLoading}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle>Recent activity</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          </CardHeader>
          <CardContent>
            <EmptyState
              icon={BarChart3}
              title="Audit and outbox stream"
              description="A combined audit-log, worker-run and ingestion-outbox feed will land here once the read endpoints are added."
              hint="Available today: GET /healthz/worker (run metrics and outbox counts)."
              className="border-0 bg-transparent py-10 shadow-none"
            />
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
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                MFA
              </div>
              <div className="mt-0.5 font-medium">
                {user?.mfa_enabled === true ? 'Enabled' : user?.mfa_enabled === false ? 'Not enrolled' : 'Bootstrap admin'}
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
  )
}
