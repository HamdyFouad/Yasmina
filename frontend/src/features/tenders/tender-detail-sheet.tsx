import * as React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Sparkles,
  Truck,
  Users,
} from 'lucide-react'
import { toast } from 'sonner'

import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { apiErrorMessage } from '@/lib/api-error'
import { unitLabel } from '@/lib/labels'
import { fetchOffers, matchTender } from '@/lib/offers-api'
import { downloadTenderDocument, fetchTender } from '@/lib/tenders-api'
import {
  formatCurrency,
  formatDate,
  formatDateTime,
  formatPercent,
  formatQuantity,
  titleCase,
} from '@/lib/utils'
import type { CustomerOffer, TenderItem } from '@/types/domain'

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-foreground">{children}</dd>
    </div>
  )
}

function Section({
  title,
  description,
  action,
  children,
}: {
  title: string
  description?: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
          {description !== undefined ? (
            <p className="text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function positionAttributes(item: TenderItem): string[] {
  const attributes: string[] = []
  if (item.material !== null) {
    attributes.push(titleCase(item.material))
  }
  if (item.color !== null) {
    attributes.push(titleCase(item.color))
  }
  if (item.glove_size !== 'NOT_APPLICABLE') {
    attributes.push(`Gr. ${item.glove_size}`)
  }
  if (item.thickness_um !== null) {
    attributes.push(`${formatQuantity(item.thickness_um)} µm`)
  }
  return attributes
}

function sourceFormatLabel(value: string): string {
  return value.startsWith('GAEB_') ? value.replace('_', ' ') : titleCase(value)
}

function OfferPricing({ offer }: { offer: CustomerOffer }) {
  return (
    <details className="group rounded-md border border-border bg-background">
      <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-3 p-3 [&::-webkit-details-marker]:hidden">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-medium">{offer.offer_number}</span>
            <StatusBadge status={offer.status} />
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {offer.lines.length} line(s) · margin {formatPercent(offer.margin_rate)} · valid until{' '}
            {formatDate(offer.valid_until)}
          </div>
        </div>
        <div className="flex items-center gap-4 text-right">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Net</div>
            <div className="text-sm font-medium">
              {formatCurrency(offer.total_net, offer.currency)}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Gross</div>
            <div className="text-sm font-semibold">
              {formatCurrency(offer.total_gross, offer.currency)}
            </div>
          </div>
        </div>
      </summary>
      <div className="space-y-3 border-t border-border p-3">
        <dl className="grid gap-2 text-sm sm:grid-cols-3">
          <MetaItem label="Net total">{formatCurrency(offer.total_net, offer.currency)}</MetaItem>
          <MetaItem label="VAT">{formatCurrency(offer.total_vat, offer.currency)}</MetaItem>
          <MetaItem label="Gross total">
            {formatCurrency(offer.total_gross, offer.currency)}
          </MetaItem>
        </dl>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Pos.</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Unit net</TableHead>
              <TableHead className="text-right">Total net</TableHead>
              <TableHead className="text-right">VAT</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {offer.lines.map((line) => (
              <TableRow key={line.id}>
                <TableCell className="font-mono text-xs">{line.position_number}</TableCell>
                <TableCell className="max-w-72">
                  <span className="line-clamp-2">{line.description}</span>
                </TableCell>
                <TableCell className="whitespace-nowrap text-right">
                  {formatQuantity(line.quantity)} {unitLabel(line.unit)}
                </TableCell>
                <TableCell className="text-right">
                  {formatCurrency(line.unit_price_net, offer.currency)}
                </TableCell>
                <TableCell className="text-right">
                  {formatCurrency(line.total_net, offer.currency)}
                </TableCell>
                <TableCell className="text-right">{formatPercent(line.vat_rate)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </details>
  )
}

function TenderDetailBody({ tenderId }: { tenderId: string }) {
  const detail = useQuery({
    queryKey: ['tender', tenderId],
    queryFn: () => fetchTender(tenderId),
  })
  const offers = useQuery({
    queryKey: ['tender-offers', tenderId],
    queryFn: () => fetchOffers({ tenderId, limit: 200 }),
  })
  const match = useMutation({
    mutationFn: (id: string) => matchTender(id, 5),
  })
  const [downloading, setDownloading] = React.useState(false)

  const handleDownload = async (filename: string | null): Promise<void> => {
    setDownloading(true)
    try {
      await downloadTenderDocument(tenderId, filename ?? 'tender-document')
      toast.success('Document downloaded')
    } catch (error) {
      toast.error('Download failed', { description: apiErrorMessage(error) })
    } finally {
      setDownloading(false)
    }
  }

  if (detail.isLoading) {
    return (
      <div className="flex-1 min-h-0 space-y-3 overflow-y-auto p-5">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  if (detail.isError || detail.data === undefined) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
        <AlertTriangle className="h-6 w-6 text-destructive" />
        <p className="text-sm font-medium">Tender could not be loaded</p>
        <p className="max-w-sm text-xs text-muted-foreground">
          {apiErrorMessage(detail.error)}
        </p>
        <Button variant="outline" size="sm" onClick={() => void detail.refetch()}>
          <RefreshCw />
          Retry
        </Button>
      </div>
    )
  }

  const { tender, document } = detail.data
  const matchResults = match.data?.tender_id === tenderId ? match.data.matches : null
  const offerList = offers.data?.offers ?? []
  const warningSource = tender.metadata['pdf_warnings']
  const warnings: string[] = Array.isArray(warningSource)
    ? warningSource.filter((value): value is string => typeof value === 'string')
    : []

  return (
    <div className="flex-1 min-h-0 space-y-6 overflow-y-auto p-5">
      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <MetaItem label="Client">{tender.client_name}</MetaItem>
        <MetaItem label="Submission deadline">{formatDate(tender.submission_deadline)}</MetaItem>
        <MetaItem label="Procedure">{titleCase(tender.procedure)}</MetaItem>
        <MetaItem label="Source format">{sourceFormatLabel(tender.source_format)}</MetaItem>
        <MetaItem label="Owner">{tender.owner ?? 'Unassigned'}</MetaItem>
        <MetaItem label="Currency">{tender.currency}</MetaItem>
        <MetaItem label="CPV codes">
          {tender.cpv_codes.length > 0 ? tender.cpv_codes.join(', ') : '—'}
        </MetaItem>
        <MetaItem label="Published">{formatDate(tender.publication_date)}</MetaItem>
        <MetaItem label="Positions">{tender.items.length.toLocaleString('de-DE')}</MetaItem>
      </dl>

      {tender.description !== null ? (
        <p className="text-sm text-muted-foreground">{tender.description}</p>
      ) : null}

      {warnings.length > 0 ? (
        <div className="flex gap-2 rounded-md border border-warning/30 bg-warning/5 p-3 text-xs text-warning">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <div className="font-medium">Parser warnings</div>
            <ul className="mt-1 list-inside list-disc">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      <Separator />

      <Section
        title="Source document"
        description="Original GAEB/PDF file stored in object storage."
      >
        {document.available ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background p-3">
            <div className="flex min-w-0 items-center gap-2">
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate text-sm">{document.filename ?? 'Stored document'}</span>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={downloading}
              onClick={() => void handleDownload(document.filename)}
            >
              {downloading ? <Loader2 className="animate-spin" /> : <Download />}
              Download
            </Button>
          </div>
        ) : (
          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
            This tender has no stored source document (for example, it was imported through an API).
          </p>
        )}
      </Section>

      <Section
        title="Positions"
        description={`${tender.items.length} normalized line item(s) parsed from the tender.`}
      >
        {tender.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No positions were extracted.</p>
        ) : (
          <div className="rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Pos.</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead className="text-right">Quantity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tender.items.map((item) => {
                  const attributes = positionAttributes(item)
                  return (
                    <TableRow key={item.id}>
                      <TableCell className="align-top font-mono text-xs">
                        {item.position_number}
                      </TableCell>
                      <TableCell className="space-y-1">
                        <div className="font-medium">{item.short_text}</div>
                        {item.long_text !== null ? (
                          <div className="line-clamp-2 text-xs text-muted-foreground">
                            {item.long_text}
                          </div>
                        ) : null}
                        {attributes.length > 0 ? (
                          <div className="flex flex-wrap gap-1 pt-1">
                            {attributes.map((attribute) => (
                              <span
                                key={attribute}
                                className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                              >
                                {attribute}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right align-top">
                        {formatQuantity(item.quantity)} {unitLabel(item.unit)}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </Section>

      <Section
        title="Catalogue matching"
        description="Hybrid lexical + pgvector similarity against the article catalogue."
        action={
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={match.isPending}
            onClick={() => match.mutate(tenderId)}
          >
            {match.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Run match
          </Button>
        }
      >
        {match.isError ? (
          <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {apiErrorMessage(match.error)}
          </p>
        ) : null}
        {matchResults === null ? (
          <p className="text-sm text-muted-foreground">
            Run the matcher to rank catalogue articles for every position.
          </p>
        ) : Object.keys(matchResults).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No catalogue candidates passed the relevance threshold for this tender.
          </p>
        ) : (
          <div className="space-y-4">
            {Object.entries(matchResults).map(([position, candidates]) => (
              <div key={position} className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Position {position}
                </div>
                <div className="space-y-2">
                  {candidates.map((candidate) => (
                    <div
                      key={candidate.article_number}
                      className="rounded-md border border-border bg-background p-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="font-mono text-xs text-muted-foreground">
                            {candidate.article_number}
                          </div>
                          <div className="text-sm font-medium">{candidate.description}</div>
                        </div>
                        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
                          {formatPercent(candidate.score)}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        <span>Lexical {formatPercent(candidate.lexical_score)}</span>
                        <span>
                          Semantic{' '}
                          {candidate.semantic_score !== null
                            ? formatPercent(candidate.semantic_score)
                            : '—'}
                        </span>
                        {candidate.matched_attributes.length > 0 ? (
                          <span>Matched: {candidate.matched_attributes.join(', ')}</span>
                        ) : null}
                      </div>
                      {candidate.reasons.length > 0 ? (
                        <ul className="mt-2 list-inside list-disc text-xs text-muted-foreground">
                          {candidate.reasons.map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Pricing & linked offers"
        description="Deterministic customer offers priced from this tender."
      >
        {offers.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : offers.isError ? (
          <p className="text-sm text-destructive">{apiErrorMessage(offers.error)}</p>
        ) : offerList.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No customer offers reference this tender yet. Create one through the pricing workflow.
          </p>
        ) : (
          <div className="space-y-2">
            {offerList.map((offer) => (
              <OfferPricing key={offer.id} offer={offer} />
            ))}
          </div>
        )}
        <div className="flex items-start gap-2 rounded-md bg-muted p-3 text-xs text-muted-foreground">
          <Truck className="mt-0.5 h-4 w-4 shrink-0" />
          <p>
            Freight and pallet costs are not part of the deterministic pricing engine yet; the totals
            above contain material cost, margin and VAT only.
          </p>
        </div>
      </Section>

      <Section title="Lifecycle">
        <dl className="grid gap-4 sm:grid-cols-2">
          <MetaItem label="Created">{formatDateTime(tender.created_at)}</MetaItem>
          <MetaItem label="Last updated">{formatDateTime(tender.updated_at)}</MetaItem>
          <MetaItem label="Tender number">{tender.tender_number}</MetaItem>
          <MetaItem label="Contact">
            {tender.client_contact_email ?? '—'}
          </MetaItem>
        </dl>
      </Section>
    </div>
  )
}

export function TenderDetailSheet({
  tenderId,
  onOpenChange,
}: {
  tenderId: string | null
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={tenderId !== null} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Users className="h-3.5 w-3.5" />
            Tender detail
          </div>
          <SheetTitle>Tender workspace</SheetTitle>
          <SheetDescription>
            Metadata, source document, catalogue matches and linked pricing offers.
          </SheetDescription>
        </SheetHeader>
        {tenderId !== null ? <TenderDetailBody tenderId={tenderId} /> : null}
      </SheetContent>
    </Sheet>
  )
}
