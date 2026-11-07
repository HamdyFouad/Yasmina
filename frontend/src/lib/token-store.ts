/**
 * Session storage.
 *
 * - Access token: in memory only (never persisted), so an XSS cannot read it
 *   from storage and it disappears when the tab closes.
 * - Refresh token: `sessionStorage` (tab-scoped) because the backend returns it
 *   in the JSON body rather than setting an HttpOnly cookie. Moving it to an
 *   HttpOnly cookie is the recommended backend follow-up; until then this is a
 *   deliberate usability/security trade-off documented in the README.
 */

const REFRESH_TOKEN_KEY = 'yasmina.refresh_token'

let accessToken: string | null = null

export interface SessionTokens {
  accessToken: string
  refreshToken?: string
}

export function getAccessToken(): string | null {
  return accessToken
}

export function getRefreshToken(): string | null {
  try {
    return window.sessionStorage.getItem(REFRESH_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setSession(tokens: SessionTokens): void {
  accessToken = tokens.accessToken
  if (tokens.refreshToken) {
    try {
      window.sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refreshToken)
    } catch {
      // Storage can be unavailable (private mode); the session still works in memory.
    }
  }
}

export function clearSession(): void {
  accessToken = null
  try {
    window.sessionStorage.removeItem(REFRESH_TOKEN_KEY)
  } catch {
    // Ignore unavailable storage.
  }
}

export function hasRefreshToken(): boolean {
  return getRefreshToken() !== null
}
