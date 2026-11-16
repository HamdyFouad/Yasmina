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
  recovery_code?: string
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
  recovery_codes_remaining: number
}

export interface UserSummary {
  id: string
  username: string
  email: string | null
  roles: Role[]
  is_active: boolean
  mfa_enabled: boolean
  failed_login_attempts: number
  locked_until: string | null
  last_login_at: string | null
}

export interface UserListResponse {
  users: UserSummary[]
  count: number
}

export interface UserCreateRequest {
  username: string
  password: string
  email?: string
  roles: Role[]
}

export interface UserRolesRequest {
  roles: Role[]
}

export interface MfaSetupResponse {
  secret: string
  otpauth_uri: string
  issuer: string
  digits: number
  period: number
}

export interface MfaVerifyResponse {
  status: string
  mfa_enabled: boolean
  recovery_codes: string[]
}

export interface MfaDisableResponse {
  status: string
  mfa_enabled: boolean
}

export interface PasswordResetIssueResponse {
  status: string
  username: string
  reset_token: string
  expires_in: number
  expires_at: string
}

export type SessionStatus = 'loading' | 'authenticated' | 'unauthenticated'
