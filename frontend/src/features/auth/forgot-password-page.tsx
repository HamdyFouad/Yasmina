import * as React from 'react'
import { Link, Navigate } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, Loader2, Mail } from 'lucide-react'

import { BrandMark } from '@/components/brand'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import { requestPasswordReset } from '@/lib/auth-api'

export function ForgotPasswordPage() {
  const { status, isAuthenticated } = useAuth()
  const [username, setUsername] = React.useState('')
  const [submitting, setSubmitting] = React.useState(false)
  const [submitted, setSubmitted] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const usernameRef = React.useRef<HTMLInputElement>(null)

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    )
  }

  if (isAuthenticated) {
    return <Navigate to="/" replace />
  }

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (submitting) {
      return
    }
    if (!username.trim()) {
      usernameRef.current?.focus()
      setError('Enter your username.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await requestPasswordReset(username.trim())
      setSubmitted(true)
    } catch (caught) {
      setError(apiErrorMessage(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6 py-12">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center gap-2.5">
          <BrandMark />
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight">Yasmina</div>
            <div className="text-[11px] font-medium text-muted-foreground">
              Day 2 Operations
            </div>
          </div>
        </div>

        <Card className="border-border/80 shadow-card">
          <CardHeader className="space-y-1">
            <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Mail className="h-5 w-5" aria-hidden="true" />
            </div>
            <CardTitle className="text-lg">Forgot password</CardTitle>
            <CardDescription>
              Enter your Yasmina username. If the account exists and has an e-mail address, a
              single-use reset token will be sent to it.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {submitted ? (
              <div role="status" className="space-y-3">
                <div className="flex items-start gap-2 rounded-md border border-success/30 bg-success/5 p-3 text-sm">
                  <CheckCircle2
                    className="mt-0.5 h-4 w-4 shrink-0 text-success"
                    aria-hidden="true"
                  />
                  <p>
                    If that account exists, reset instructions have been sent. Check your inbox
                    (and spam folder) for the token.
                  </p>
                </div>
                <Button asChild className="w-full">
                  <Link to="/reset-password?token=">I have a reset token</Link>
                </Button>
              </div>
            ) : (
              <form className="space-y-4" noValidate onSubmit={(event) => void handleSubmit(event)}>
                <div className="space-y-1.5">
                  <Label htmlFor="forgot-username">Username</Label>
                  <Input
                    id="forgot-username"
                    ref={usernameRef}
                    name="username"
                    autoComplete="username"
                    autoCapitalize="none"
                    spellCheck={false}
                    required
                    autoFocus
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="operator"
                    disabled={submitting}
                    aria-invalid={error !== null ? true : undefined}
                    aria-describedby={error !== null ? 'forgot-error' : undefined}
                  />
                  <p className="text-xs text-muted-foreground">
                    The response is always the same, so this page cannot be used to discover
                    accounts.
                  </p>
                </div>

                {error !== null ? (
                  <p
                    id="forgot-error"
                    role="alert"
                    className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive"
                  >
                    {error}
                  </p>
                ) : null}

                <Button type="submit" className="w-full" disabled={submitting}>
                  {submitting ? (
                    <>
                      <Loader2 className="animate-spin" aria-hidden="true" />
                      Sending…
                    </>
                  ) : (
                    <>
                      <Mail aria-hidden="true" />
                      Send reset instructions
                    </>
                  )}
                </Button>
              </form>
            )}

            <Button asChild variant="ghost" className="w-full">
              <Link to="/login">
                <ArrowLeft aria-hidden="true" />
                Back to sign in
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
