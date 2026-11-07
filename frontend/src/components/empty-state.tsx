import type { LucideIcon } from 'lucide-react'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export function EmptyState({
  icon: Icon,
  title,
  description,
  hint,
  className,
}: {
  icon: LucideIcon
  title: string
  description: string
  hint?: string
  className?: string
}) {
  return (
    <Card className={cn('flex flex-col items-center justify-center px-6 py-14 text-center', className)}>
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Icon className="h-6 w-6" aria-hidden="true" />
      </div>
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
      {hint !== undefined ? (
        <p className="mt-3 rounded-md bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </Card>
  )
}
