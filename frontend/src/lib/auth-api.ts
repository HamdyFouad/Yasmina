import { api, refreshAccessToken } from '@/lib/api-client'
import { isApiError } from '@/lib/api-error'
import { clearSession, getRefreshToken, hasRefreshToken, setSession } from '@/lib/token-store'
import type {
  LoginRequest,
  LogoutRequest,
  MeResponse,
  TokenResponse,
} from '@/types/auth'

export function isMfaRequired(error: unknown): boolean {
  return isApiError(error) && error.mfaRequired
}

export async function login(request: LoginRequest): Promise<TokenResponse> {
  const response = await api.post<TokenResponse>('/auth/token', request)
  setSession({
    accessToken: response.data.access_token,
    ...(response.data.refresh_token ? { refreshToken: response.data.refresh_token } : {}),
  })
  return response.data
}

export async function fetchMe(): Promise<MeResponse> {
  const response = await api.get<MeResponse>('/auth/me')
  return response.data
}

export async function restoreSession(): Promise<MeResponse> {
  if (!hasRefreshToken()) {
    throw new Error('No session to restore.')
  }
  await refreshAccessToken()
  return await fetchMe()
}

export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken()
  const body: LogoutRequest = refreshToken ? { refresh_token: refreshToken } : {}
  try {
    await api.post('/auth/logout', body)
  } catch {
    // Local logout must always succeed, even if the token was already invalid.
  } finally {
    clearSession()
  }
}
