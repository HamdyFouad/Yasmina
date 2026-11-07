import * as React from 'react'

import {
  fetchMe,
  login as loginRequest,
  logout as logoutRequest,
  restoreSession,
} from '@/lib/auth-api'
import { SESSION_EXPIRED_EVENT } from '@/lib/api-client'
import { clearSession, hasRefreshToken } from '@/lib/token-store'
import type { LoginRequest, MeResponse, Role, SessionStatus } from '@/types/auth'

interface AuthContextValue {
  status: SessionStatus
  user: MeResponse | null
  roles: Role[]
  isAuthenticated: boolean
  login: (credentials: LoginRequest) => Promise<void>
  logout: () => Promise<void>
  hasRole: (...roles: Role[]) => boolean
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<SessionStatus>('loading')
  const [user, setUser] = React.useState<MeResponse | null>(null)

  React.useEffect(() => {
    let cancelled = false

    const bootstrap = async (): Promise<void> => {
      if (!hasRefreshToken()) {
        if (!cancelled) {
          setStatus('unauthenticated')
        }
        return
      }
      try {
        const me = await restoreSession()
        if (!cancelled) {
          setUser(me)
          setStatus('authenticated')
        }
      } catch {
        if (!cancelled) {
          clearSession()
          setUser(null)
          setStatus('unauthenticated')
        }
      }
    }

    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [])

  React.useEffect(() => {
    const onSessionExpired = (): void => {
      setUser(null)
      setStatus('unauthenticated')
    }
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
    }
  }, [])

  const login = React.useCallback(async (credentials: LoginRequest): Promise<void> => {
    await loginRequest(credentials)
    const me = await fetchMe()
    setUser(me)
    setStatus('authenticated')
  }, [])

  const logout = React.useCallback(async (): Promise<void> => {
    try {
      await logoutRequest()
    } finally {
      setUser(null)
      setStatus('unauthenticated')
    }
  }, [])

  const roles = React.useMemo<Role[]>(() => user?.roles ?? [], [user])

  const hasRole = React.useCallback(
    (...required: Role[]): boolean => {
      if (roles.includes('admin')) {
        return true
      }
      return required.some((role) => roles.includes(role))
    },
    [roles],
  )

  const value = React.useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      roles,
      isAuthenticated: status === 'authenticated',
      login,
      logout,
      hasRole,
    }),
    [status, user, roles, login, logout, hasRole],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = React.useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used inside <AuthProvider>.')
  }
  return context
}
