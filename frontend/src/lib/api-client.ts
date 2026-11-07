import axios, {
  type AxiosError,
  type InternalAxiosRequestConfig,
} from 'axios'
import { toast } from 'sonner'

import { toApiError, ApiError } from '@/lib/api-error'
import { API_BASE_URL, API_TIMEOUT_MS } from '@/lib/env'
import { clearSession, getAccessToken, setAccessToken } from '@/lib/token-store'
import type { TokenResponse } from '@/types/auth'

export const SESSION_EXPIRED_EVENT = 'yasmina:session-expired'

interface RetriableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
}

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT_MS,
  // Required for the HttpOnly refresh cookie; the API also enables CORS
  // credentials for separately hosted dashboards.
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

let refreshInFlight: Promise<string> | null = null

function isAuthEndpoint(url: string | undefined): boolean {
  if (!url) {
    return false
  }
  return (
    url.includes('/auth/token') ||
    url.includes('/auth/refresh') ||
    url.includes('/auth/logout')
  )
}

async function performRefresh(): Promise<string> {
  // Bare axios (not the `api` instance) prevents interceptor recursion. The
  // refresh token travels only as the HttpOnly cookie, so the request has no
  // body and must opt into credentials explicitly.
  const response = await axios.post<TokenResponse>(
    `${API_BASE_URL}/auth/refresh`,
    undefined,
    { timeout: API_TIMEOUT_MS, withCredentials: true },
  )
  setAccessToken(response.data.access_token)
  return response.data.access_token
}

/** Single-flight refresh: concurrent 401s share one refresh request. */
export function refreshAccessToken(): Promise<string> {
  if (refreshInFlight === null) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

export function expireSession(): void {
  clearSession()
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT))
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const apiError = toApiError(error)
    const config = error.config as RetriableConfig | undefined
    const status = error.response?.status ?? null

    if (status === 401 && config !== undefined && config._retry !== true && !isAuthEndpoint(config.url)) {
      config._retry = true
      try {
        const token = await refreshAccessToken()
        config.headers.set('Authorization', `Bearer ${token}`)
        return await api.request(config)
      } catch {
        expireSession()
        return Promise.reject(
          new ApiError('Your session has expired. Please sign in again.', {
            status: 401,
          }),
        )
      }
    }

    if (status === 429) {
      const retryAfter = apiError.retryAfterSeconds
      toast.error('Rate limit reached', {
        description:
          retryAfter !== null
            ? `Too many requests. Retry in ${Math.max(1, Math.ceil(retryAfter))} second(s).`
            : 'Too many requests. Please slow down and try again.',
      })
    }

    if (status === 403) {
      toast.error('Permission denied', {
        description: apiError.detail,
      })
    }

    return Promise.reject(apiError)
  },
)
