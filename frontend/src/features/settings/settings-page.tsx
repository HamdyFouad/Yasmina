import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { CheckCircle2, Copy, KeyRound, Loader2, ShieldCheck, ShieldOff } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { toast } from 'sonner'

import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import { disableMfa, setupMfa, verifyMfaEnrolment } from '@/lib/auth-api'

function SectionError({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive"
    >
      {message}
    </p>
  )
}

function DisableMfaDialog({
  onClose,
  onDisabled,
}: {
  onClose: () => void
  onDisabled: () => void
}) {
  const passwordRef = React.useRef<HTMLInputElement>(null)
  const codeRef = React.useRef<HTMLInputElement>(null)
  const [password, setPassword] = React.useState('')
  const [code, setCode] = React.useState('')
  const [attempted, setAttempted] = React.useState(false)
  const passwordError =
    attempted && password.length === 0 ? 'Enter your current password.' : undefined
  const codeError =
    attempted && !/^\d{6,8}$/.test(code.trim())
      ? 'Enter the 6- or 8-digit code from your authenticator app.'
      : undefined
  const disable = useMutation({
    mutationFn: () => disableMfa(password, code),
    onSuccess: () => {
      toast.success('MFA disabled')
      onDisabled()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (disable.isPending) {
      return
    }
    if (password.length === 0) {
      passwordRef.current?.focus()
      return
    }
    if (!/^\d{6,8}$/.test(code.trim())) {
      codeRef.current?.focus()
      return
    }
    disable.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Disable multi-factor authentication?</DialogTitle>
            <DialogDescription>
              Your account will be protected by the password alone, and the remaining recovery
              codes are deleted. Confirm with your password and the current authenticator code.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5">
            <Label htmlFor="mfa-disable-password">Password</Label>
            <Input
              id="mfa-disable-password"
              ref={passwordRef}
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={passwordError !== undefined ? true : undefined}
              aria-describedby={
                passwordError !== undefined ? 'mfa-disable-password-error' : undefined
              }
              disabled={disable.isPending}
            />
            {passwordError !== undefined ? (
              <p id="mfa-disable-password-error" className="text-xs text-destructive">
                {passwordError}
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="mfa-disable-code">Authenticator code</Label>
            <p id="mfa-disable-code-hint" className="text-xs text-muted-foreground">
              Confirm with the current 6-digit code from your authenticator app.
            </p>
            <Input
              id="mfa-disable-code"
              ref={codeRef}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6,8}"
              maxLength={8}
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
              aria-invalid={codeError !== undefined ? true : undefined}
              aria-describedby={
                codeError !== undefined
                  ? 'mfa-disable-code-hint mfa-disable-code-error'
                  : 'mfa-disable-code-hint'
              }
              disabled={disable.isPending}
            />
            {codeError !== undefined ? (
              <p id="mfa-disable-code-error" className="text-xs text-destructive">
                {codeError}
              </p>
            ) : null}
          </div>

          {disable.isError ? <SectionError message={apiErrorMessage(disable.error)} /> : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              autoFocus
              disabled={disable.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" variant="destructive" disabled={disable.isPending}>
              {disable.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <ShieldOff aria-hidden="true" />
              )}
              Disable MFA
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function SettingsPage() {
  const { user, refreshUser } = useAuth()
  const codeRef = React.useRef<HTMLInputElement>(null)
  const [setup, setSetup] = React.useState<Awaited<ReturnType<typeof setupMfa>> | null>(null)
  const [recoveryCodes, setRecoveryCodes] = React.useState<string[] | null>(null)
  const [acknowledged, setAcknowledged] = React.useState(false)
  const [code, setCode] = React.useState('')
  const [codeTouched, setCodeTouched] = React.useState(false)
  const [announcement, setAnnouncement] = React.useState('')
  const [disableOpen, setDisableOpen] = React.useState(false)

  const codeValid = /^\d{6,8}$/.test(code.trim())
  const codeError =
    codeTouched && code.trim().length > 0 && !codeValid
      ? 'Enter the 6- or 8-digit code from your authenticator app.'
      : undefined

  const startSetup = useMutation({
    mutationFn: setupMfa,
    onSuccess: (data) => {
      setSetup(data)
      setRecoveryCodes(null)
      setAcknowledged(false)
      setCode('')
      setCodeTouched(false)
      setAnnouncement('MFA setup started. Scan the QR code and enter the current code.')
    },
  })

  const verify = useMutation({
    mutationFn: verifyMfaEnrolment,
    onSuccess: async (data) => {
      setRecoveryCodes(data.recovery_codes)
      setSetup(null)
      setAcknowledged(false)
      setCode('')
      setAnnouncement(
        'MFA enabled. Store the recovery codes, then acknowledge that you saved them.',
      )
      await refreshUser()
    },
  })

  const bootstrapAccount = user?.mfa_enabled === null
  const mfaEnabled = user?.mfa_enabled === true

  const handleVerify = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setCodeTouched(true)
    if (!codeValid) {
      codeRef.current?.focus()
      return
    }
    if (verify.isPending) {
      return
    }
    verify.mutate(code.trim())
  }

  const copyRecoveryCodes = async (): Promise<void> => {
    if (recoveryCodes === null) {
      return
    }
    try {
      await navigator.clipboard.writeText(recoveryCodes.join('\n'))
      setAnnouncement('Recovery codes copied to the clipboard.')
      toast.success('Recovery codes copied')
    } catch {
      toast.error('Could not copy the recovery codes')
    }
  }

  const finishSetup = (): void => {
    setRecoveryCodes(null)
    setAcknowledged(false)
    setAnnouncement('MFA setup complete. Keep the recovery codes in a safe place.')
    toast.success('MFA enabled')
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Account settings"
        description="Manage your account security. Multi-factor authentication protects privileged approvals."
      />

      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
          <CardDescription>Signed-in operator and assigned roles.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Username</div>
            <div className="font-medium">{user?.subject ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Roles</div>
            <div className="font-medium">{user?.roles.join(', ') ?? '—'}</div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Multi-factor authentication</CardTitle>
          <CardDescription>
            Time-based one-time codes (TOTP) from an authenticator app protect this account.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {bootstrapAccount ? (
            <p className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
              The bootstrap admin account cannot enrol MFA. Create a database administrator account
              and enrol there instead.
            </p>
          ) : null}

          {recoveryCodes !== null ? (
            <section aria-labelledby="recovery-heading" className="space-y-3">
              <h2 id="recovery-heading" className="text-sm font-semibold">
                Recovery codes
              </h2>
              <p className="text-sm text-muted-foreground">
                These codes are shown once. Each code signs you in a single time if the
                authenticator app is unavailable. Store them offline.
              </p>
              <ul className="grid gap-2 rounded-md border border-border bg-muted/40 p-3 font-mono text-sm sm:grid-cols-2">
                {recoveryCodes.map((recoveryCode) => (
                  <li key={recoveryCode}>{recoveryCode}</li>
                ))}
              </ul>
              <Button type="button" variant="outline" size="sm" onClick={() => void copyRecoveryCodes()}>
                <Copy aria-hidden="true" />
                Copy codes
              </Button>

              <div className="flex items-start gap-2">
                <input
                  id="recovery-acknowledged"
                  type="checkbox"
                  className="mt-1 h-4 w-4 rounded border-border"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                  required
                />
                <Label htmlFor="recovery-acknowledged" className="font-normal">
                  I have stored these recovery codes in a safe place. They will not be shown
                  again.
                </Label>
              </div>

              <Button type="button" disabled={!acknowledged} onClick={finishSetup}>
                <CheckCircle2 aria-hidden="true" />
                Finish setup
              </Button>
            </section>
          ) : setup !== null ? (
            <section aria-labelledby="setup-heading" className="space-y-4">
              <h2 id="setup-heading" className="text-sm font-semibold">
                Scan the QR code
              </h2>
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
                <div className="w-fit rounded-md bg-white p-3">
                  <QRCodeSVG
                    value={setup.otpauth_uri}
                    size={168}
                    level="M"
                    aria-hidden="true"
                  />
                </div>
                <div className="space-y-2 text-sm">
                  <p className="text-muted-foreground">
                    Scan with Google Authenticator, 1Password, Authy or a compatible app.
                  </p>
                  <div>
                    <div className="text-xs uppercase tracking-wide text-muted-foreground">
                      Manual key
                    </div>
                    <code className="break-all font-mono text-xs">{setup.secret}</code>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wide text-muted-foreground">
                      Provisioning URI
                    </div>
                    <code className="break-all font-mono text-[11px] text-muted-foreground">
                      {setup.otpauth_uri}
                    </code>
                  </div>
                </div>
              </div>

              <form className="space-y-3" noValidate onSubmit={handleVerify}>
                <div className="space-y-1.5">
                  <Label htmlFor="mfa-enrol-code">Authenticator code</Label>
                  <p id="mfa-enrol-code-hint" className="text-xs text-muted-foreground">
                    Enter the current 6-digit code shown by the app.
                  </p>
                  <Input
                    id="mfa-enrol-code"
                    ref={codeRef}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    autoFocus
                    maxLength={8}
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    onBlur={() => setCodeTouched(true)}
                    required
                    aria-invalid={codeError !== undefined ? true : undefined}
                    aria-describedby={
                      codeError !== undefined
                        ? 'mfa-enrol-code-hint mfa-enrol-code-error'
                        : 'mfa-enrol-code-hint'
                    }
                  />
                  {codeError !== undefined ? (
                    <p id="mfa-enrol-code-error" className="text-xs text-destructive">
                      {codeError}
                    </p>
                  ) : null}
                </div>
                {verify.isError ? (
                  <SectionError message={apiErrorMessage(verify.error)} />
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button type="submit" disabled={verify.isPending}>
                    {verify.isPending ? (
                      <Loader2 className="animate-spin" aria-hidden="true" />
                    ) : (
                      <ShieldCheck aria-hidden="true" />
                    )}
                    Verify and enable
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={verify.isPending}
                    onClick={() => setSetup(null)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </section>
          ) : mfaEnabled ? (
            <div className="space-y-3">
              <p className="flex items-center gap-2 text-sm">
                <ShieldCheck className="h-4 w-4 text-success" aria-hidden="true" />
                MFA is enabled.
                {user !== null && user.recovery_codes_remaining >= 0 ? (
                  <span className="text-muted-foreground">
                    {user.recovery_codes_remaining} recovery code(s) left.
                  </span>
                ) : null}
              </p>
              <Button type="button" variant="outline" onClick={() => setDisableOpen(true)}>
                <ShieldOff aria-hidden="true" />
                Disable MFA
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                MFA is not enabled. Enrolling requires an authenticator app and one live code.
              </p>
              {startSetup.isError ? (
                <SectionError message={apiErrorMessage(startSetup.error)} />
              ) : null}
              <Button
                type="button"
                disabled={startSetup.isPending || bootstrapAccount}
                onClick={() => startSetup.mutate()}
              >
                {startSetup.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <KeyRound aria-hidden="true" />
                )}
                Enable MFA
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {disableOpen ? (
        <DisableMfaDialog
          onClose={() => setDisableOpen(false)}
          onDisabled={() => {
            setDisableOpen(false)
            setAnnouncement('MFA disabled.')
            void refreshUser()
          }}
        />
      ) : null}
    </div>
  )
}
