import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  KeyRound,
  Loader2,
  Lock,
  RefreshCw,
  ShieldCheck,
  Unlock,
  UserPlus,
  Users,
} from 'lucide-react'
import { toast } from 'sonner'

import { EmptyState } from '@/components/empty-state'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
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
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/auth-context'
import { apiErrorMessage } from '@/lib/api-error'
import {
  createUser,
  fetchUsers,
  issueUserPasswordReset,
  lockUser,
  unlockUser,
  updateUserRoles,
} from '@/lib/users-api'
import { formatDateTime, titleCase } from '@/lib/utils'
import type { PasswordResetIssueResponse, Role, UserSummary } from '@/types/auth'

const ROLE_OPTIONS: Role[] = ['admin', 'approver', 'sales', 'viewer']
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const MIN_PASSWORD_LENGTH = 12

function statusFor(user: UserSummary): { label: string; tone: 'success' | 'warning' | 'destructive' } {
  if (!user.is_active) {
    return { label: 'Locked', tone: 'destructive' }
  }
  if (user.locked_until !== null && Date.parse(user.locked_until) > Date.now()) {
    return { label: `Locked until ${formatDateTime(user.locked_until)}`, tone: 'warning' }
  }
  return { label: 'Active', tone: 'success' }
}

function RoleCheckboxes({
  idPrefix,
  selected,
  onChange,
  disabled,
  error,
}: {
  idPrefix: string
  selected: Role[]
  onChange: (roles: Role[]) => void
  disabled?: boolean | undefined
  error?: string | undefined
}) {
  const toggle = (role: Role): void => {
    onChange(
      selected.includes(role) ? selected.filter((value) => value !== role) : [...selected, role],
    )
  }
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Roles</legend>
      <div className="flex flex-wrap gap-3">
        {ROLE_OPTIONS.map((role) => {
          const id = `${idPrefix}-role-${role}`
          return (
            <div key={role} className="flex items-center gap-2">
              <input
                id={id}
                type="checkbox"
                className="h-4 w-4 rounded border-border"
                checked={selected.includes(role)}
                onChange={() => toggle(role)}
                disabled={disabled}
                aria-invalid={error !== undefined ? true : undefined}
                aria-describedby={error !== undefined ? `${idPrefix}-roles-error` : undefined}
              />
              <Label htmlFor={id} className="font-normal">
                {titleCase(role)}
              </Label>
            </div>
          )
        })}
      </div>
      {error !== undefined ? (
        <p id={`${idPrefix}-roles-error`} className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </fieldset>
  )
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const usernameRef = React.useRef<HTMLInputElement>(null)
  const emailRef = React.useRef<HTMLInputElement>(null)
  const passwordRef = React.useRef<HTMLInputElement>(null)
  const [username, setUsername] = React.useState('')
  const [email, setEmail] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [roles, setRoles] = React.useState<Role[]>(['viewer'])
  const [touched, setTouched] = React.useState<ReadonlySet<string>>(new Set())
  const [attempted, setAttempted] = React.useState(false)

  const errors: Record<string, string> = {}
  if (!username.trim()) errors['username'] = 'Enter a username.'
  else if (username.trim().length > 128) errors['username'] = 'Use 128 characters or fewer.'
  if (email.trim() && !EMAIL_RE.test(email.trim())) {
    errors['email'] = 'Enter an e-mail address like name@example.com.'
  }
  if (password.length < MIN_PASSWORD_LENGTH) {
    errors['password'] = `Use at least ${MIN_PASSWORD_LENGTH} characters.`
  }
  if (roles.length === 0) errors['roles'] = 'Select at least one role.'

  const errorFor = (field: string): string | undefined =>
    attempted || touched.has(field) ? errors[field] : undefined

  const create = useMutation({
    mutationFn: () =>
      createUser({
        username: username.trim(),
        password,
        roles,
        ...(email.trim() ? { email: email.trim() } : {}),
      }),
    onSuccess: async (user) => {
      toast.success('User created', { description: user.username })
      await queryClient.invalidateQueries({ queryKey: ['auth', 'users'] })
      onClose()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (create.isPending) {
      return
    }
    if (errors['username'] !== undefined) {
      usernameRef.current?.focus()
      return
    }
    if (errors['email'] !== undefined) {
      emailRef.current?.focus()
      return
    }
    if (errors['password'] !== undefined) {
      passwordRef.current?.focus()
      return
    }
    if (errors['roles'] !== undefined) {
      document.getElementById('create-role-admin')?.focus()
      return
    }
    create.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <form className="space-y-4" noValidate onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Create user</DialogTitle>
            <DialogDescription>
              Provision a database account. The password can be changed by the user later.
            </DialogDescription>
          </DialogHeader>

          {attempted && Object.keys(errors).length > 0 ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive"
            >
              Fix the highlighted fields before creating the user.
            </p>
          ) : null}

          <div className="space-y-1.5">
            <Label htmlFor="create-username">Username</Label>
            <Input
              id="create-username"
              ref={usernameRef}
              autoComplete="off"
              maxLength={128}
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              onBlur={() => setTouched((current) => new Set(current).add('username'))}
              aria-invalid={errorFor('username') !== undefined ? true : undefined}
              aria-describedby={errorFor('username') !== undefined ? 'create-username-error' : undefined}
              disabled={create.isPending}
            />
            {errorFor('username') !== undefined ? (
              <p id="create-username-error" className="text-xs text-destructive">
                {errorFor('username')}
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="create-email">E-mail (optional)</Label>
            <Input
              id="create-email"
              ref={emailRef}
              type="email"
              autoComplete="email"
              maxLength={320}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              onBlur={() => setTouched((current) => new Set(current).add('email'))}
              aria-invalid={errorFor('email') !== undefined ? true : undefined}
              aria-describedby={errorFor('email') !== undefined ? 'create-email-error' : undefined}
              disabled={create.isPending}
            />
            {errorFor('email') !== undefined ? (
              <p id="create-email-error" className="text-xs text-destructive">
                {errorFor('email')}
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="create-password">Initial password</Label>
            <p id="create-password-hint" className="text-xs text-muted-foreground">
              At least {MIN_PASSWORD_LENGTH} characters. Share it out of band.
            </p>
            <Input
              id="create-password"
              ref={passwordRef}
              type="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              onBlur={() => setTouched((current) => new Set(current).add('password'))}
              aria-invalid={errorFor('password') !== undefined ? true : undefined}
              aria-describedby={
                errorFor('password') !== undefined
                  ? 'create-password-hint create-password-error'
                  : 'create-password-hint'
              }
              disabled={create.isPending}
            />
            {errorFor('password') !== undefined ? (
              <p id="create-password-error" className="text-xs text-destructive">
                {errorFor('password')}
              </p>
            ) : null}
          </div>

          <RoleCheckboxes
            idPrefix="create"
            selected={roles}
            onChange={setRoles}
            disabled={create.isPending}
            error={errorFor('roles')}
          />

          {create.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {apiErrorMessage(create.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              autoFocus
              disabled={create.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <UserPlus aria-hidden="true" />
              )}
              Create user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EditRolesDialog({ user, onClose }: { user: UserSummary; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [roles, setRoles] = React.useState<Role[]>(user.roles)
  const [attempted, setAttempted] = React.useState(false)
  const rolesError = attempted && roles.length === 0 ? 'Select at least one role.' : undefined

  const update = useMutation({
    mutationFn: () => updateUserRoles(user.username, roles),
    onSuccess: async () => {
      toast.success('Roles updated', { description: user.username })
      await queryClient.invalidateQueries({ queryKey: ['auth', 'users'] })
      onClose()
    },
  })

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setAttempted(true)
    if (roles.length === 0) {
      document.getElementById('edit-role-admin')?.focus()
      return
    }
    if (update.isPending) {
      return
    }
    update.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Edit roles for {user.username}</DialogTitle>
            <DialogDescription>
              Roles decide which routes and actions this account can use.
            </DialogDescription>
          </DialogHeader>

          <RoleCheckboxes
            idPrefix="edit"
            selected={roles}
            onChange={setRoles}
            disabled={update.isPending}
            error={rolesError}
          />

          {update.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {apiErrorMessage(update.error)}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              autoFocus
              disabled={update.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Save roles
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function PasswordResetDialog({ user, onClose }: { user: UserSummary; onClose: () => void }) {
  const [issued, setIssued] = React.useState<PasswordResetIssueResponse | null>(null)
  const reset = useMutation({
    mutationFn: () => issueUserPasswordReset(user.username),
    onSuccess: (data) => {
      setIssued(data)
      toast.success('Reset token issued', { description: user.username })
    },
  })

  const copy = async (): Promise<void> => {
    if (issued === null) {
      return
    }
    try {
      await navigator.clipboard.writeText(issued.reset_token)
      toast.success('Reset token copied')
    } catch {
      toast.error('Could not copy the reset token')
    }
  }

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Force a password reset for {user.username}?</DialogTitle>
          <DialogDescription>
            This issues a single-use token. Share it with the user over a trusted channel; they
            choose a new password with it.
          </DialogDescription>
        </DialogHeader>

        {issued !== null ? (
          <div className="space-y-2">
            <p className="text-sm font-medium">One-time reset token</p>
            <code className="block break-all rounded-md border border-border bg-muted/40 p-3 font-mono text-xs">
              {issued.reset_token}
            </code>
            <p className="text-xs text-muted-foreground">
              Expires {formatDateTime(issued.expires_at)}.
            </p>
            <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
              <KeyRound aria-hidden="true" />
              Copy token
            </Button>
          </div>
        ) : null}

        {reset.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {apiErrorMessage(reset.error)}
          </p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" autoFocus onClick={onClose}>
            {issued !== null ? 'Close' : 'Cancel'}
          </Button>
          {issued === null ? (
            <Button
              type="button"
              disabled={reset.isPending}
              onClick={() => reset.mutate()}
            >
              {reset.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <KeyRound aria-hidden="true" />
              )}
              Issue reset token
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function LockDialog({
  user,
  lock,
  onClose,
}: {
  user: UserSummary
  lock: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const action = useMutation({
    mutationFn: async () => (lock ? await lockUser(user.username) : await unlockUser(user.username)),
    onSuccess: async () => {
      toast.success(lock ? 'Account locked' : 'Account unlocked', { description: user.username })
      await queryClient.invalidateQueries({ queryKey: ['auth', 'users'] })
      onClose()
    },
  })

  return (
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {lock ? `Lock ${user.username}?` : `Unlock ${user.username}?`}
          </DialogTitle>
          <DialogDescription>
            {lock
              ? 'The account cannot sign in and its refresh sessions are revoked. You can unlock it later.'
              : 'The account becomes active again and its lockout counters are cleared.'}
          </DialogDescription>
        </DialogHeader>

        {action.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {apiErrorMessage(action.error)}
          </p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" autoFocus onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant={lock ? 'destructive' : 'default'}
            disabled={action.isPending}
            onClick={() => action.mutate()}
          >
            {action.isPending ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : lock ? (
              <Lock aria-hidden="true" />
            ) : (
              <Unlock aria-hidden="true" />
            )}
            {lock ? 'Lock account' : 'Unlock account'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function UsersPage() {
  const { user: currentUser } = useAuth()
  const captionId = React.useId()
  const [createOpen, setCreateOpen] = React.useState(false)
  const [rolesTarget, setRolesTarget] = React.useState<UserSummary | null>(null)
  const [resetTarget, setResetTarget] = React.useState<UserSummary | null>(null)
  const [lockTarget, setLockTarget] = React.useState<{ user: UserSummary; lock: boolean } | null>(
    null,
  )

  const usersQuery = useQuery({
    queryKey: ['auth', 'users'],
    queryFn: fetchUsers,
  })

  const rows = usersQuery.data?.users ?? []
  const total = usersQuery.data?.count ?? 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Users"
        description="Database accounts, their roles, MFA state and lockout status."
        actions={
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={usersQuery.isFetching}
              onClick={() => void usersQuery.refetch()}
            >
              {usersQuery.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Refresh
            </Button>
            <Button type="button" size="sm" onClick={() => setCreateOpen(true)}>
              <UserPlus aria-hidden="true" />
              Create user
            </Button>
          </div>
        }
      />

      <p className="text-xs text-muted-foreground">{total.toLocaleString('de-DE')} account(s)</p>

      {usersQuery.isLoading ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </CardContent>
        </Card>
      ) : usersQuery.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <AlertTriangle className="h-6 w-6 text-destructive" aria-hidden="true" />
            <div role="alert">
              <p className="text-sm font-medium">Could not load users</p>
              <p className="text-xs text-muted-foreground">{apiErrorMessage(usersQuery.error)}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void usersQuery.refetch()}>
              <RefreshCw aria-hidden="true" />
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No user accounts"
          description="Create the first database account to delegate operations."
          hint="The bootstrap admin is configured through environment variables and is not listed."
        />
      ) : (
        <Card>
          <div
            role="region"
            aria-labelledby={captionId}
            aria-busy={usersQuery.isFetching}
            tabIndex={0}
            className="stacked-table max-h-[70vh] overflow-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <table className="w-full min-w-[960px] border-collapse text-sm">
              <caption id={captionId} className="sr-only">
                User accounts with roles, MFA and lockout state
              </caption>
              <thead className="sticky top-0 z-10 bg-card shadow-sm">
                <tr className="border-b border-border">
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    User
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Roles
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    MFA
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Status
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-left align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Last login
                  </th>
                  <th
                    scope="col"
                    className="h-10 px-3 text-right align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const isSelf = row.username === currentUser?.subject
                  const status = statusFor(row)
                  return (
                    <tr
                      key={row.id}
                      className="border-b border-border transition-colors last:border-0 hover:bg-muted/50"
                    >
                      <th
                        scope="row"
                        className="px-3 py-3 text-left align-top text-sm font-medium"
                      >
                        {row.username}
                        {isSelf ? (
                          <span className="ml-2 text-xs font-normal text-muted-foreground">(you)</span>
                        ) : null}
                        {row.email !== null ? (
                          <span className="block font-normal text-xs text-muted-foreground">
                            {row.email}
                          </span>
                        ) : null}
                      </th>
                      <td data-label="Roles" className="px-3 py-3 align-top">
                        <div className="flex flex-wrap gap-1">
                          {row.roles.map((role) => (
                            <Badge key={role} variant="secondary">
                              {titleCase(role)}
                            </Badge>
                          ))}
                        </div>
                      </td>
                      <td data-label="MFA" className="px-3 py-3 align-top">
                        <Badge variant={row.mfa_enabled ? 'success' : 'secondary'}>
                          {row.mfa_enabled ? 'Enabled' : 'Off'}
                        </Badge>
                      </td>
                      <td data-label="Status" className="px-3 py-3 align-top">
                        <Badge variant={status.tone}>{status.label}</Badge>
                      </td>
                      <td data-label="Last login" className="px-3 py-3 align-top text-xs">
                        {row.last_login_at !== null ? formatDateTime(row.last_login_at) : 'Never'}
                      </td>
                      <td data-label="Actions" className="px-3 py-3 align-top">
                        <div className="flex flex-wrap items-center justify-end gap-1">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            aria-label={`Edit roles for ${row.username}`}
                            disabled={isSelf}
                            onClick={() => setRolesTarget(row)}
                          >
                            Roles
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            aria-label={`Force password reset for ${row.username}`}
                            onClick={() => setResetTarget(row)}
                          >
                            <KeyRound aria-hidden="true" />
                            Reset
                          </Button>
                          {row.is_active ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-label={`Lock account ${row.username}`}
                              className="text-destructive"
                              disabled={isSelf}
                              onClick={() => setLockTarget({ user: row, lock: true })}
                            >
                              <Lock aria-hidden="true" />
                              Lock
                            </Button>
                          ) : (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-label={`Unlock account ${row.username}`}
                              onClick={() => setLockTarget({ user: row, lock: false })}
                            >
                              <Unlock aria-hidden="true" />
                              Unlock
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
        Locking an account revokes its refresh sessions. Access tokens expire within 15 minutes.
      </p>

      {createOpen ? <CreateUserDialog onClose={() => setCreateOpen(false)} /> : null}
      {rolesTarget !== null ? (
        <EditRolesDialog user={rolesTarget} onClose={() => setRolesTarget(null)} />
      ) : null}
      {resetTarget !== null ? (
        <PasswordResetDialog user={resetTarget} onClose={() => setResetTarget(null)} />
      ) : null}
      {lockTarget !== null ? (
        <LockDialog
          key={`${lockTarget.user.username}-${String(lockTarget.lock)}`}
          user={lockTarget.user}
          lock={lockTarget.lock}
          onClose={() => setLockTarget(null)}
        />
      ) : null}
    </div>
  )
}
