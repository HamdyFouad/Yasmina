import { Link } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

export function NotFoundPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <Card className="max-w-md p-8 text-center">
        <div className="text-4xl font-semibold tracking-tight">404</div>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you are looking for does not exist in the Yasmina operations dashboard.
        </p>
        <Button asChild className="mt-6">
          <Link to="/">Back to overview</Link>
        </Button>
      </Card>
    </div>
  )
}
