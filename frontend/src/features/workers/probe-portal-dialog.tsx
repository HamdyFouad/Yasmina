import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Play, ShieldCheck } from 'lucide-react'

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
import { probePortalTarget } from '@/lib/ops-api'
import type { PortalTargetSummary, ProbeReport } from '@/types/domain'

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  )
}

export function ProbePortalDialog({
  portals,
  onClose,
}: {
  portals: PortalTargetSummary[]
  onClose: () => void
}) {
  const selectRef = React.useRef<HTMLButtonElement>(null)
  const [portalId, setPortalId] = React.useState(portals[0]?.id ?? '')
  const [maxPages, setMaxPages] = React.useState('1')
  const [report, setReport] = React.useState<ProbeReport | null>(null)
  const [validationError, setValidationError] = React.useState<string | null>(null)

  const probe = useMutation({
    mutationFn: () => probePortalTarget(portalId, Number.parseInt(maxPages, 10)),
    onSuccess: (data) => {
      setReport(data)
      setValidationError(null)
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (probe.isPending) {
      return
    }
    const pages = Number.parseInt(maxPages, 10)
    if (portalId.length === 0) {
      setValidationError('Choose a portal to probe.')
      selectRef.current?.focus()
      return
    }
    if (!Number.isInteger(pages) || pages < 1 || pages > 5) {
      setValidationError('Enter a whole number of pages between 1 and 5.')
      return
    }
    setValidationError(null)
    probe.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent
        className="max-h-[92vh] overflow-y-auto sm:max-w-3xl"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          selectRef.current?.focus()
        }}
      >
        <DialogHeader>
          <DialogTitle>Run portal probe</DialogTitle>
          <DialogDescription>
            Fetches one configured portal read-only: no database writes, no MinIO archive, no
            ingestion job. It parses detail pages for HTML-visible position rows and reports
            extraction diagnostics.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
            <div className="space-y-1">
              <Label htmlFor="probe-portal">Portal</Label>
              <p id="probe-portal-hint" className="text-xs text-muted-foreground">
                Enabled and discovery entries from `config/portals.yaml`.
              </p>
              <Select value={portalId} onValueChange={setPortalId} disabled={probe.isPending}>
                <SelectTrigger
                  id="probe-portal"
                  ref={selectRef}
                  aria-describedby="probe-portal-hint"
                >
                  <SelectValue placeholder="Select a portal" />
                </SelectTrigger>
                <SelectContent>
                  {portals.map((portal) => (
                    <SelectItem key={portal.id} value={portal.id}>
                      {portal.name} ({portal.id}){portal.enabled ? '' : ' · disabled'}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="probe-pages">Pages</Label>
              <p id="probe-pages-hint" className="text-xs text-muted-foreground">
                1–5 listing pages.
              </p>
              <Input
                id="probe-pages"
                name="maxPages"
                inputMode="numeric"
                value={maxPages}
                onChange={(event) => setMaxPages(event.target.value)}
                aria-describedby="probe-pages-hint"
                disabled={probe.isPending}
              />
            </div>
          </div>

          {validationError !== null ? (
            <p role="alert" className="text-xs text-destructive">
              {validationError}
            </p>
          ) : null}

          {probe.isError ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive"
            >
              {apiErrorMessage(probe.error)}
            </p>
          ) : null}

          {report !== null ? (
            <div className="space-y-4 rounded-md border border-border bg-muted/20 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p
                  role="status"
                  className="flex flex-wrap items-center gap-2 text-sm font-medium"
                >
                  <ShieldCheck className="h-4 w-4 text-success" aria-hidden="true" />
                  Read-only probe complete: {report.portal.name}
                </p>
                <span className="text-xs text-muted-foreground">
                  {report.elapsed_seconds.toFixed(2)} s
                </span>
              </div>

              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Metric label="Notices discovered" value={report.notices_discovered} />
                <Metric label="Notices parsed" value={report.notices_parsed} />
                <Metric label="Detail pages" value={report.detail_pages_parsed} />
                <Metric label="Position rows" value={report.positions_parsed} />
              </dl>

              <p className="text-xs text-muted-foreground">
                {report.notices_with_positions.toLocaleString('de-DE')} notice(s) exposed
                HTML position rows; {report.details_fetched.toLocaleString('de-DE')} detail page(s)
                were fetched during discovery.
              </p>

              <dl className="grid grid-cols-3 gap-3 text-xs">
                <Metric label="CPV skipped" value={report.skipped.cpv} />
                <Metric label="Invalid skipped" value={report.skipped.invalid} />
                <Metric label="Stale skipped" value={report.skipped.stale} />
              </dl>

              {report.zero_results ? (
                <p
                  role="alert"
                  className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive"
                >
                  Zero results: the probe completed but parsed no notices. Check the start URL,
                  API preference, robots enforcement and selectors.
                </p>
              ) : null}

              {report.zero_positions ? (
                <p
                  role="status"
                  className="rounded-md border border-warning/40 bg-warning/5 p-2 text-xs text-warning"
                >
                  No HTML position rows were found. Line items may be inside a GAEB/PDF
                  attachment that the ingestion worker validates separately.
                </p>
              ) : null}

              {report.warnings.length > 0 ? (
                <div
                  role="status"
                  className="rounded-md border border-warning/40 bg-warning/5 p-2 text-xs text-warning"
                >
                  <p className="font-medium">Warnings</p>
                  <ul className="mt-1 list-inside list-disc space-y-1">
                    {report.warnings.map((warning, index) => (
                      <li key={`${index}-${warning}`}>{warning}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {report.errors.length > 0 ? (
                <div
                  role="alert"
                  className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive"
                >
                  <p className="flex items-center gap-1 font-medium">
                    <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                    Probe errors
                  </p>
                  <ul className="mt-1 list-inside list-disc space-y-1">
                    {report.errors.map((error, index) => (
                      <li key={`${index}-${error}`}>{error}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {report.issues.length > 0 ? (
                <div className="space-y-2">
                  <h3 className="text-sm font-medium">Extraction issues</h3>
                  <div className="stacked-table max-h-64 overflow-auto rounded-md border border-border">
                    <table className="w-full min-w-[520px] border-collapse text-left text-xs">
                      <caption className="sr-only">
                        Probe extraction issues with URL, code and message
                      </caption>
                      <thead className="sticky top-0 bg-card">
                        <tr className="border-b border-border">
                          <th scope="col" className="px-2 py-1 font-medium uppercase">
                            Code
                          </th>
                          <th scope="col" className="px-2 py-1 font-medium uppercase">
                            URL
                          </th>
                          <th scope="col" className="px-2 py-1 font-medium uppercase">
                            Message
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.issues.map((issue, index) => (
                          <tr
                            key={`${index}-${issue.code}-${issue.url}`}
                            className="border-b last:border-0"
                          >
                            <td data-label="Code" className="px-2 py-1 align-top font-mono">
                              {issue.code}
                            </td>
                            <td data-label="URL" className="max-w-64 px-2 py-1 align-top">
                              <span className="line-clamp-2 break-all">{issue.url}</span>
                            </td>
                            <td data-label="Message" className="px-2 py-1 align-top">
                              {issue.message}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {report.issues_truncated > 0 ? (
                    <p className="text-xs text-muted-foreground">
                      {report.issues_truncated.toLocaleString('de-DE')} additional issue(s)
                      truncated.
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={probe.isPending}>
              Close
            </Button>
            <Button type="submit" disabled={probe.isPending || portals.length === 0}>
              {probe.isPending ? <Loader2 className="animate-spin" /> : <Play />}
              {probe.isPending ? 'Probing…' : 'Run probe'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
