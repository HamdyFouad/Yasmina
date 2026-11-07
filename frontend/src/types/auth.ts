/**
 * Auth and session types mirroring the FastAPI auth routes.
 * `Role` values match `src/models/enums.py::Role`.
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
  refresh_token?: string
  refresh_expires_in?: number
}

export interface RefreshTokenRequest {
  refresh_token: string
}

export interface LogoutRequest {
  refresh_token?: string
}

export interface MeResponse {
  subject: string
  roles: Role[]
  mfa_enabled: boolean | null
}

export type SessionStatus = 'loading' | 'authenticated' | 'unauthenticated'
