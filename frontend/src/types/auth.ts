/**
 * Auth and session types mirroring the FastAPI auth routes.
 * `Role` values match `src/models/enums.py::Role`.
 *
 * Refresh tokens are never part of a JSON response: the API sets an HttpOnly
 * cookie, so the browser stores and sends it automatically.
 */
export type Role = 'admin' | 'sales' | 'approver' | 'viewer'

export interface LoginRequest {
  username: string
  password: string
  totp_code?: string
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  roles: Role[]
}

export interface MeResponse {
  subject: string
  roles: Role[]
  mfa_enabled: boolean | null
}

export type SessionStatus = 'loading' | 'authenticated' | 'unauthenticated'
