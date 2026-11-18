import { ChevronLeft, ChevronRight } from 'lucide-react'

import { Button } from '@/components/ui/button'

export function TablePagination({
  total,
  page,
  pageSize,
  shown,
  isFetching,
  onPageChange,
}: {
  total: number
  page: number
  pageSize: number
  shown: number
  isFetching: boolean
  onPageChange: (page: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const offset = page * pageSize
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = offset + shown

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-muted-foreground">
        {total === 0 ? 'No results' : `Showing ${rangeStart}–${rangeEnd} of ${total}`}
      </p>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page === 0 || isFetching}
          onClick={() => onPageChange(Math.max(0, page - 1))}
        >
          <ChevronLeft aria-hidden="true" />
          Previous
        </Button>
        <span className="text-xs text-muted-foreground">
          Page {page + 1} of {pageCount}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page + 1 >= pageCount || isFetching}
          onClick={() => onPageChange(page + 1)}
        >
          Next
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}
