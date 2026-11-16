import { api } from '@/lib/api-client'
import type {
  PasswordResetIssueResponse,
  Role,
  UserCreateRequest,
  UserListResponse,
  UserRolesRequest,
  UserSummary,
} from '@/types/auth'

export async function fetchUsers(): Promise<UserListResponse> {
  const response = await api.get<UserListResponse>('/auth/users')
  return response.data
}

export async function createUser(payload: UserCreateRequest): Promise<UserSummary> {
  const response = await api.post<{ user: UserSummary }>('/auth/users', payload)
  return response.data.user
}

export async function updateUserRoles(
  username: string,
  roles: Role[],
): Promise<UserSummary> {
  const payload: UserRolesRequest = { roles }
  const response = await api.patch<{ user: UserSummary }>(
    `/auth/users/${encodeURIComponent(username)}/roles`,
    payload,
  )
  return response.data.user
}

export async function lockUser(username: string): Promise<UserSummary> {
  const response = await api.post<{ user: UserSummary }>(
    `/auth/users/${encodeURIComponent(username)}/lock`,
  )
  return response.data.user
}

export async function unlockUser(username: string): Promise<UserSummary> {
  const response = await api.post<{ user: UserSummary }>(
    `/auth/users/${encodeURIComponent(username)}/unlock`,
  )
  return response.data.user
}

export async function issueUserPasswordReset(
  username: string,
): Promise<PasswordResetIssueResponse> {
  const response = await api.post<PasswordResetIssueResponse>(
    `/auth/users/${encodeURIComponent(username)}/password/reset`,
  )
  return response.data
}
