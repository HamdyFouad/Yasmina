import { useQuery } from '@tanstack/react-query'
import {
  BarChart3,
  CircleDollarSign,
  FileText,
  Percent,
  RefreshCw,
  TrendingUp,
  Trophy,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { apiErrorMessage } from '@/lib/api-error'
import {
  fetchReportingAging,
  fetchReportingMargins,
  fetchReportingSummary,
} from '@/lib/reporting-api'
import { formatCurrency, formatPercent, titleCase } from '@/lib/utils'
import type { MarginBreakdown, PipelineStage } from '@/types/domain'

function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = 'default',
}: {
  label: string
  value: string | number
  hint: string
  icon: LucideIcon
  tone?: 'default' | 'success' | 'warning' | 'destructive'
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
          <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <p
          className={`mt-1 text-2xl font-semibold tracking-tight ${
            tone === 'destructive'
              ? 'text-destructive'
              : tone === 'warning'
                ? 'text-warning'
                : tone === 'success'
                  ? 'text-success'
                  : ''
          }`}
        >
          {value}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  )
}

function BarList({
  rows,
  value,
  max,
}: {
  rows: MarginBreakdown[]
  value: (row: MarginBreakdown) => number
  max: number
}) {
  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <li key={`${row.dimension}-${row.label}`} className="space-y-1">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate font-medium" title={row.label}>
              {row.label}
            </span>
            <span className="shrink-0 text-muted-foreground">
              {formatCurrency(row.margin_net)} · {formatPercent(row.margin_rate ?? 0)}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${max > 0 ? Math.max(0, Math.min(100, (value(row) / max) * 100)) : 0}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

function MarginCard({ title, rows }: { title: string; rows: MarginBreakdown[] }) {
  const max = Math.max(...rows.map((row) => Math.abs(Number(row.margin_net))), 0)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">No offer margin data yet.</p>
        ) : (
          <BarList
            rows={rows}
            value={(row) => Math.abs(Number(row.margin_net))}
            max={max}
          />
        )}
      </CardContent>
    </Card>
  )
}

function pipelineWidth(stage: PipelineStage, max: number): number {
  return max > 0 ? Math.max(2, Math.round((stage.count / max) * 100)) : 0
}

export function ReportingPage() {
  const summary = useQuery({
    queryKey: ['reporting', 'summary'],
    queryFn: fetchReportingSummary,
  })
  const margins = useQuery({
    queryKey: ['reporting', 'margins'],
    queryFn: fetchReportingMargins,
  })
  const aging = useQuery({
    queryKey: ['reporting', 'aging'],
    queryFn: fetchReportingAging,
  })

  const refresh = (): void => {
    void summary.refetch()
    void margins.refetch()
    void aging.refetch()
  }

  const kpis = summary.data?.kpis
  const stages = summary.data?.pipeline ?? []
  const maxStage = Math.max(...stages.map((stage) => stage.count), 0)
  const winLoss = summary.data?.win_loss
  const totalDecided = (winLoss?.won_count ?? 0) + (winLoss?.lost_count ?? 0)
  const wonShare = totalDecided > 0 ? (winLoss?.won_count ?? 0) / totalDecided : 0
  const agingMax = Math.max(
    ...(aging.data?.buckets ?? []).map((bucket) => Math.abs(Number(bucket.outstanding))),
    0,
  )

  return (
    <div className="space-y-8">
      <PageHeader
        title="Reporting"
        description="Pipeline conversion, quoted margin and invoice aging for commercial control."
        actions={
          <Button type="button" variant="outline" size="sm" onClick={refresh}>
            <RefreshCw />
            Refresh
          </Button>
        }
      />

      {summary.isError ? (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
          {apiErrorMessage(summary.error)}
        </p>
      ) : null}

      <section aria-labelledby="reporting-kpis-heading" className="space-y-3">
        <h2 id="reporting-kpis-heading" className="text-lg font-semibold tracking-tight">
          Commercial KPIs
        </h2>
        <div className="reporting-metric-container">
          <div className="reporting-metric-grid">
            <MetricCard
              label="Pipeline value"
              value={
                summary.isLoading || kpis === undefined
                  ? '—'
                  : formatCurrency(kpis.pipeline_value, kpis.currency)
              }
              hint="Draft, pending, approved and sent offers."
              icon={BarChart3}
            />
            <MetricCard
              label="Win rate"
              value={
                summary.isLoading || kpis === undefined
                  ? '—'
                  : kpis.win_rate !== null
                    ? formatPercent(kpis.win_rate)
                    : '—'
              }
              hint="Accepted vs rejected/expired offers."
              icon={Trophy}
              tone="success"
            />
            <MetricCard
              label="Outstanding invoices"
              value={
                summary.isLoading || kpis === undefined
                  ? '—'
                  : formatCurrency(kpis.outstanding, kpis.currency)
              }
              hint="Gross invoiced less payments and credits."
              icon={Percent}
              tone="warning"
            />
            <MetricCard
              label="Overdue"
              value={
                summary.isLoading || kpis === undefined
                  ? '—'
                  : formatCurrency(kpis.overdue_amount, kpis.currency)
              }
              hint={`${kpis?.tenders ?? 0} tenders · ${kpis?.offers ?? 0} offers`}
              icon={CircleDollarSign}
              tone={kpis?.overdue_amount !== undefined && kpis.overdue_amount !== '0.00' ? 'destructive' : 'default'}
            />
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section aria-labelledby="pipeline-heading" className="space-y-3">
          <h2 id="pipeline-heading" className="text-lg font-semibold tracking-tight">
            Pipeline
          </h2>
          <Card>
            <CardContent className="space-y-3 p-4">
              {(summary.isLoading ? Array.from({ length: 5 }) : stages).map((stage, index) => {
                if (summary.isLoading) {
                  return <Skeleton key={index} className="h-8 w-full" />
                }
                const item = stage as PipelineStage
                return (
                  <div key={item.status} className="space-y-1">
                    <div className="flex items-baseline justify-between gap-2 text-xs">
                      <span className="font-medium">{titleCase(item.status)}</span>
                      <span className="text-muted-foreground">
                        {item.count} · {formatCurrency(item.total_net, kpis?.currency)}
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${pipelineWidth(item, maxStage)}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </CardContent>
          </Card>
        </section>

        <section aria-labelledby="win-loss-heading" className="space-y-3">
          <h2 id="win-loss-heading" className="text-lg font-semibold tracking-tight">
            Win / loss
          </h2>
          <Card>
            <CardContent className="flex flex-col items-center gap-4 p-5 sm:flex-row">
              <div
                role="img"
                aria-label={`Won ${winLoss?.won_count ?? 0} offers; lost ${winLoss?.lost_count ?? 0} offers`}
                className="relative h-32 w-32 shrink-0 rounded-full"
                style={{
                  background:
                    totalDecided > 0
                      ? `conic-gradient(hsl(var(--success)) 0 ${wonShare * 100}%, hsl(var(--destructive)) ${wonShare * 100}% 100%)`
                      : 'hsl(var(--muted))',
                }}
              >
                <div className="absolute inset-4 flex flex-col items-center justify-center rounded-full bg-card text-center">
                  <span className="text-xl font-semibold">{formatPercent(winLoss?.win_rate ?? 0)}</span>
                  <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    win rate
                  </span>
                </div>
              </div>
              <dl className="grid flex-1 grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-muted-foreground">Won</dt>
                  <dd className="font-semibold text-success">{winLoss?.won_count ?? 0}</dd>
                  <dd className="text-xs text-muted-foreground">
                    {formatCurrency(winLoss?.won_value ?? 0, kpis?.currency)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Lost</dt>
                  <dd className="font-semibold text-destructive">{winLoss?.lost_count ?? 0}</dd>
                  <dd className="text-xs text-muted-foreground">
                    {formatCurrency(winLoss?.lost_value ?? 0, kpis?.currency)}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>
        </section>
      </div>

      <section aria-labelledby="conversion-heading" className="space-y-3">
        <h2 id="conversion-heading" className="text-lg font-semibold tracking-tight">
          Tender conversion
        </h2>
        <div className="grid gap-4 sm:grid-cols-3">
          <MetricCard
            label="Tender coverage"
            value={
              summary.data?.conversion.tender_coverage_rate !== null &&
              summary.data?.conversion.tender_coverage_rate !== undefined
                ? formatPercent(summary.data.conversion.tender_coverage_rate)
                : '—'
            }
            hint={`${summary.data?.conversion.tenders_with_offers ?? 0} of ${summary.data?.conversion.total_tenders ?? 0} tenders have an offer.`}
            icon={FileText}
          />
          <MetricCard
            label="Offer conversion"
            value={
              summary.data?.conversion.offer_conversion_rate !== null &&
              summary.data?.conversion.offer_conversion_rate !== undefined
                ? formatPercent(summary.data.conversion.offer_conversion_rate)
                : '—'
            }
            hint="Accepted offers divided by all offers."
            icon={TrendingUp}
            tone="success"
          />
          <MetricCard
            label="Tender win rate"
            value={
              summary.data?.conversion.tender_win_rate !== null &&
              summary.data?.conversion.tender_win_rate !== undefined
                ? formatPercent(summary.data.conversion.tender_win_rate)
                : '—'
            }
            hint="Tenders with an accepted offer divided by tenders with offers."
            icon={Trophy}
          />
        </div>
      </section>

      <section aria-labelledby="margin-heading" className="space-y-3">
        <h2 id="margin-heading" className="text-lg font-semibold tracking-tight">
          Margin by customer, supplier and tender
        </h2>
        <p className="text-sm text-muted-foreground">
          Quoted margin from offer lines: revenue net minus the cost basis stored on each line.
        </p>
        {margins.isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : margins.isError ? (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
            {apiErrorMessage(margins.error)}
          </p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-3">
            <MarginCard title="Customer" rows={margins.data?.customers ?? []} />
            <MarginCard title="Supplier" rows={margins.data?.suppliers ?? []} />
            <MarginCard title="Tender" rows={margins.data?.tenders ?? []} />
          </div>
        )}
      </section>

      <section aria-labelledby="aging-heading" className="space-y-3">
        <h2 id="aging-heading" className="text-lg font-semibold tracking-tight">
          Invoice aging
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="Paid"
            value={
              aging.data !== undefined
                ? formatCurrency(aging.data.paid_total, aging.data.currency)
                : '—'
            }
            hint="Fully paid invoices."
            icon={CircleDollarSign}
            tone="success"
          />
          <MetricCard
            label="Outstanding"
            value={
              aging.data !== undefined
                ? formatCurrency(aging.data.outstanding_total, aging.data.currency)
                : '—'
            }
            hint="Gross less payments and credits."
            icon={CircleDollarSign}
            tone="warning"
          />
          <MetricCard
            label="Overdue"
            value={
              aging.data !== undefined
                ? formatCurrency(aging.data.overdue_total, aging.data.currency)
                : '—'
            }
            hint={`${aging.data?.invoice_count ?? 0} invoices in aging.`}
            icon={CircleDollarSign}
            tone="destructive"
          />
          <MetricCard
            label="Invoices in aging"
            value={aging.data?.invoice_count ?? 0}
            hint="Issued invoices in the paid/outstanding aging report."
            icon={FileText}
          />
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Outstanding by due-date bucket</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {aging.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : aging.isError ? (
              <p role="alert" className="text-xs text-destructive">
                {apiErrorMessage(aging.error)}
              </p>
            ) : (
              (aging.data?.buckets ?? []).map((bucket) => (
                <div key={bucket.label} className="space-y-1">
                  <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
                    <span className="font-medium">{bucket.label}</span>
                    <span className="text-muted-foreground">
                      {bucket.invoice_count} invoice(s) ·{' '}
                      {formatCurrency(bucket.outstanding, aging.data?.currency)}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                    <div
                      className="h-full rounded-full bg-warning"
                      style={{
                        width: `${agingMax > 0 ? Math.max(0, Math.min(100, (Math.abs(Number(bucket.outstanding)) / agingMax) * 100)) : 0}%`,
                      }}
                    />
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  )
}
