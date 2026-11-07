import * as React from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight, KeyRound, Loader2, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'

import { BrandMark } from '@/components/brand'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/features/auth/auth-context'
import { isMfaRequired } from '@/lib/auth-api'
import { isApiError } from '@/lib/api-error'

interface LoginLocationState {
  from?: string
}

export function LoginPage() {
  const { status, isAuthenticated, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [totpCode, setTotpCode] = React.useState('')
  const [needsMfa, setNeedsMfa] = React.useState(false)
  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const redirectTo = (location.state as LoginLocationState | null)?.from ?? '/'

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    )
  }

  if (isAuthenticated) {
    return <Navigate to={redirectTo} replace />
  }

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (submitting) {
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await login({
        username: username.trim(),
        password,
        ...(needsMfa && totpCode.trim().length > 0 ? { totp_code: totpCode.trim() } : {}),
      })
      toast.success('Signed in', { description: `Welcome back, ${username.trim()}.` })
      navigate(redirectTo, { replace: true })
    } catch (caught) {
      if (isMfaRequired(caught)) {
        setNeedsMfa(true)
        setError('This account requires a time-based one-time code (TOTP).')
      } else if (isApiError(caught)) {
        setError(caught.detail)
      } else {
        setError('Unexpected error while signing in.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-slate-950 p-10 text-slate-100 lg:flex">
        <div
          className="pointer-events-none absolute inset-0 opacity-70"
          style={{
            background:
              'radial-gradient(60% 50% at 20% 10%, rgba(59,130,246,0.35), transparent 60%), radial-gradient(50% 40% at 90% 90%, rgba(16,185,129,0.22), transparent 60%)',
          }}
        />
        <div className="relative flex items-center gap-2.5">
          <BrandMark className="bg-white/10 text-white shadow-none ring-1 ring-white/20" />
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight">Yasmina</div>
            <div className="text-[11px] font-medium text-slate-400">Day 2 Operations</div>
          </div>
        </div>

        <div className="relative max-w-md space-y-6">
          <h1 className="text-balance text-3xl font-semibold tracking-tight">
            Operate the entire tender pipeline from one calm workspace.
          </h1>
          <p className="text-sm leading-relaxed text-slate-300">
            Track discovered tenders, inspect pgvector catalogue matches, verify deterministic
            pricing and manage GoBD-compliant invoices.
          </p>
          <ul className="space-y-3 text-sm text-slate-300">
            <li className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              Short-lived JWTs with rotating refresh sessions and RBAC.
            </li>
            <li className="flex items-start gap-3">
              <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" />
              TOTP multi-factor authentication for privileged accounts.
            </li>
          </ul>
        </div>

        <p className="relative text-xs text-slate-500">
          BayerClean · Regensburg · Proprietary software
        </p>
      </aside>

      <main className="flex items-center justify-center bg-background px-6 py-12">
        <div className="w-full max-w-sm space-y-6">
          <div className="flex items-center gap-2.5 lg:hidden">
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
              <CardTitle className="text-lg">Sign in</CardTitle>
              <CardDescription>
                Use your Yasmina operator account. Sessions are short-lived and rotate
                automatically.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-4" onSubmit={(event) => void handleSubmit(event)}>
                <div className="space-y-1.5">
                  <Label htmlFor="username">Username</Label>
                  <Input
                    id="username"
                    name="username"
                    autoComplete="username"
                    autoFocus
                    required
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="admin"
                    disabled={submitting}
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="password">Password</Label>
                  <Input
                    id="password"
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    disabled={submitting}
                  />
                </div>

                {needsMfa ? (
                  <div className="space-y-1.5">
                    <Label htmlFor="totp">Authenticator code</Label>
                    <Input
                      id="totp"
                      name="totp"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      pattern="[0-9]{6,8}"
                      maxLength={8}
                      required
                      value={totpCode}
                      onChange={(event) => setTotpCode(event.target.value)}
                      placeholder="123456"
                      disabled={submitting}
                    />
                    <p className="text-xs text-muted-foreground">
                      Enter the current 6–8 digit code from your authenticator app.
                    </p>
                  </div>
                ) : null}

                {error !== null ? (
                  <div
                    role="alert"
                    className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                  >
                    {error}
                  </div>
                ) : null}

                <Button type="submit" className="w-full" disabled={submitting}>
                  {submitting ? (
                    <>
                      <Loader2 className="animate-spin" />
                      Signing in…
                    </>
                  ) : (
                    <>
                      Sign in
                      <ArrowRight />
                    </>
                  )}
                </Button>
              </form>
            </CardContent>
          </Card>

          <p className="text-center text-xs text-muted-foreground">
            Protected by JWT authentication, RBAC and rate limiting.
          </p>
        </div>
      </main>
    </div>
  )
}
