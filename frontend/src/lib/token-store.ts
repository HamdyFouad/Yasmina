/**
 * In-memory access-token store.
 *
 * The access token deliberately lives in a module variable (never
 * `localStorage`/`sessionStorage`), so an XSS cannot read a persisted bearer
 * token. The refresh token is an HttpOnly cookie managed entirely by the API,
 * which means a page reload restores the session by calling `/auth/refresh`
 * with `withCredentials: true`.
 */

let accessToken: string | null = null

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string): void {
  accessToken = token
}

export function clearSession(): void {
  accessToken = null
}
