import { Badge } from '@/components/ui/badge'
import { statusTone } from '@/lib/status'
import { cn, titleCase } from '@/lib/utils'

export function StatusBadge({
  status,
  className,
}: {
  status: string
  className?: string
}) {
  return (
    <Badge variant={statusTone(status)} className={cn('whitespace-nowrap', className)}>
      {titleCase(status)}
    </Badge>
  )
}
