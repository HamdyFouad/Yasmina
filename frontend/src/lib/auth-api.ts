import { api, refreshAccessToken } from '@/lib/api-client'
import { isApiError } from '@/lib/api-error'
import { clearSession, setAccessToken } from '@/lib/token-store'
import type { LoginRequest, MeResponse, TokenResponse } from '@/types/auth'

export function isMfaRequired(error: unknown): boolean {
  return isApiError(error) && error.mfaRequired
}

export async function login(request: LoginRequest): Promise<TokenResponse> {
  const response = await api.post<TokenResponse>('/auth/token', request)
  setAccessToken(response.data.access_token)
  return response.data
}

export async function fetchMe(): Promise<MeResponse> {
  const response = await api.get<MeResponse>('/auth/me')
  return response.data
}

/**
 * Restore a page-reload session: the refresh token is an HttpOnly cookie, so
 * always try to rotate it before reading the current user.
 */
export async function restoreSession(): Promise<MeResponse> {
  await refreshAccessToken()
  return await fetchMe()
}

export async function logout(): Promise<void> {
  try {
    // No body: the API reads and clears the HttpOnly refresh cookie.
    await api.post('/auth/logout')
  } catch {
    // Local logout must always succeed, even if the cookie was already invalid.
  } finally {
    clearSession()
  }
}
