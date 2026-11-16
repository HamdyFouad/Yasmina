import * as React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CheckCircle2, KeyRound, Loader2 } from 'lucide-react'

import { BrandMark } from '@/components/brand'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiErrorMessage } from '@/lib/api-error'
import { resetPassword } from '@/lib/auth-api'

const MIN_PASSWORD_LENGTH = 12

export function ResetPasswordPage() {
  const [searchParams] = useSearchParams()
  const [token, setToken] = React.useState(() => searchParams.get('token') ?? '')
  const [password, setPassword] = React.useState('')
  const [confirmation, setConfirmation] = React.useState('')
  const [touched, setTouched] = React.useState<ReadonlySet<string>>(new Set())
  const [attempted, setAttempted] = React.useState(false)
  const [submitting, setSubmitting] = React.useState(false)
  const [resetComplete, setResetComplete] = React.useState(false)
  const [serverError, setServerError] = React.useState<string | null>(null)
  const tokenRef = React.useRef<HTMLInputElement>(null)
  const passwordRef = React.useRef<HTMLInputElement>(null)
  const confirmationRef = React.useRef<HTMLInputElement>(null)

  const errors: Record<string, string> = {}
  if (token.trim().length < 20) {
    errors['token'] = 'Paste the reset token from your e-mail.'
  }
  if (password.length < MIN_PASSWORD_LENGTH) {
    errors['password'] = `Use at least ${MIN_PASSWORD_LENGTH} characters.`
  }
  if (confirmation !== password) {
    errors['confirmation'] = 'The passwords do not match.'
  }
  const errorFor = (field: string): string | undefined =>
    attempted || touched.has(field) ? errors[field] : undefined

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setAttempted(true)
    setServerError(null)
    if (submitting) {
      return
    }
    if (errors['token'] !== undefined) {
      tokenRef.current?.focus()
      return
    }
    if (errors['password'] !== undefined) {
      passwordRef.current?.focus()
      return
    }
    if (errors['confirmation'] !== undefined) {
      confirmationRef.current?.focus()
      return
    }
    setSubmitting(true)
    try {
      await resetPassword(token.trim(), password)
      setResetComplete(true)
    } catch (caught) {
      setServerError(apiErrorMessage(caught))
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
              <KeyRound className="h-5 w-5" aria-hidden="true" />
            </div>
            <CardTitle className="text-lg">Reset password</CardTitle>
            <CardDescription>
              Paste the single-use token from your e-mail and choose a new password.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {resetComplete ? (
              <div role="status" className="space-y-3">
                <div className="flex items-start gap-2 rounded-md border border-success/30 bg-success/5 p-3 text-sm">
                  <CheckCircle2
                    className="mt-0.5 h-4 w-4 shrink-0 text-success"
                    aria-hidden="true"
                  />
                  <p>Your password was reset. All refresh sessions have been revoked.</p>
                </div>
                <Button asChild className="w-full">
                  <Link to="/login">Sign in with your new password</Link>
                </Button>
              </div>
            ) : (
              <form className="space-y-4" noValidate onSubmit={(event) => void handleSubmit(event)}>
                <div className="space-y-1.5">
                  <Label htmlFor="reset-token">Reset token</Label>
                  <Input
                    id="reset-token"
                    ref={tokenRef}
                    name="token"
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    required
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                    placeholder="Paste the token from your e-mail"
                    disabled={submitting}
                    aria-invalid={errorFor('token') !== undefined ? true : undefined}
                    aria-describedby={errorFor('token') !== undefined ? 'reset-token-error' : undefined}
                  />
                  {errorFor('token') !== undefined ? (
                    <p id="reset-token-error" role="alert" className="text-xs text-destructive">
                      {errorFor('token')}
                    </p>
                  ) : null}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="reset-password">New password</Label>
                  <Input
                    id="reset-password"
                    ref={passwordRef}
                    name="new-password"
                    type="password"
                    autoComplete="new-password"
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    onBlur={() => setTouched((current) => new Set(current).add('password'))}
                    disabled={submitting}
                    aria-invalid={errorFor('password') !== undefined ? true : undefined}
                    aria-describedby={
                      errorFor('password') !== undefined ? 'reset-password-error' : undefined
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    At least {MIN_PASSWORD_LENGTH} characters.
                  </p>
                  {errorFor('password') !== undefined ? (
                    <p id="reset-password-error" role="alert" className="text-xs text-destructive">
                      {errorFor('password')}
                    </p>
                  ) : null}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="reset-confirmation">Confirm new password</Label>
                  <Input
                    id="reset-confirmation"
                    ref={confirmationRef}
                    name="confirm-password"
                    type="password"
                    autoComplete="new-password"
                    required
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    onBlur={() => setTouched((current) => new Set(current).add('confirmation'))}
                    disabled={submitting}
                    aria-invalid={errorFor('confirmation') !== undefined ? true : undefined}
                    aria-describedby={
                      errorFor('confirmation') !== undefined
                        ? 'reset-confirmation-error'
                        : undefined
                    }
                  />
                  {errorFor('confirmation') !== undefined ? (
                    <p
                      id="reset-confirmation-error"
                      role="alert"
                      className="text-xs text-destructive"
                    >
                      {errorFor('confirmation')}
                    </p>
                  ) : null}
                </div>

                {serverError !== null ? (
                  <p
                    role="alert"
                    className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive"
                  >
                    {serverError}
                  </p>
                ) : null}

                <Button type="submit" className="w-full" disabled={submitting}>
                  {submitting ? (
                    <>
                      <Loader2 className="animate-spin" aria-hidden="true" />
                      Resetting…
                    </>
                  ) : (
                    <>
                      <KeyRound aria-hidden="true" />
                      Reset password
                    </>
                  )}
                </Button>
              </form>
            )}

            <Button asChild variant="ghost" className="w-full">
              <Link to="/login">Back to sign in</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
