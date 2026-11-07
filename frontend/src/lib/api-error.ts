import axios from 'axios'

interface FastApiValidationIssue {
  loc?: unknown[]
  msg?: string
  type?: string
}

function extractDetail(data: unknown): string | null {
  if (typeof data === 'string') {
    return data
  }
  if (typeof data !== 'object' || data === null) {
    return null
  }
  const record = data as Record<string, unknown>
  const detail = record['detail']
  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((issue) => {
        if (typeof issue !== 'object' || issue === null) {
          return null
        }
        const typed = issue as FastApiValidationIssue
        return typeof typed.msg === 'string' ? typed.msg : null
      })
      .filter((message): message is string => message !== null)
    if (messages.length > 0) {
      return messages.join('; ')
    }
  }
  return null
}

function parseRetryAfter(headers: unknown): number | null {
  if (typeof headers !== 'object' || headers === null) {
    return null
  }
  const raw = (headers as Record<string, unknown>)['retry-after']
  if (typeof raw !== 'string' || raw.trim() === '') {
    return null
  }
  const seconds = Number.parseFloat(raw)
  return Number.isFinite(seconds) ? seconds : null
}

export class ApiError extends Error {
  readonly status: number | null
  readonly detail: string
  readonly retryAfterSeconds: number | null
  readonly mfaRequired: boolean

  constructor(
    message: string,
    options: {
      status?: number | null
      detail?: string
      retryAfterSeconds?: number | null
      mfaRequired?: boolean
    } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status ?? null
    this.detail = options.detail ?? message
    this.retryAfterSeconds = options.retryAfterSeconds ?? null
    this.mfaRequired = options.mfaRequired ?? false
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

/** Human-readable message for a caught query/mutation error. */
export function apiErrorMessage(value: unknown): string {
  if (isApiError(value)) {
    return value.detail
  }
  if (value instanceof Error) {
    return value.message
  }
  return 'Unexpected error while talking to the API.'
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  if (axios.isAxiosError(error)) {
    const response = error.response
    if (response) {
      const detail = extractDetail(response.data)
      const headerValue = response.headers['x-yasmina-mfa-required']
      const mfaRequired =
        typeof headerValue === 'string' && headerValue.toLowerCase() === 'true'
      return new ApiError(detail ?? `Request failed with status ${response.status}`, {
        status: response.status,
        detail: detail ?? `Request failed with status ${response.status}`,
        retryAfterSeconds: parseRetryAfter(response.headers),
        mfaRequired,
      })
    }
    return new ApiError(
      error.code === 'ECONNABORTED'
        ? 'The API did not respond in time. Please try again.'
        : 'Cannot reach the Yasmina API. Check that the backend is running.',
    )
  }
  if (error instanceof Error) {
    return new ApiError(error.message)
  }
  return new ApiError('Unexpected error while talking to the API.')
}
