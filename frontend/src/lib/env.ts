const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()

/**
 * Default to same-origin `/api/v1`, which the Vite dev server proxies to
 * `http://127.0.0.1:8000`. Set `VITE_API_BASE_URL` to the absolute backend URL
 * when the dashboard is hosted separately (CORS must then allow this origin).
 */
export const API_BASE_URL = (configuredBaseUrl && configuredBaseUrl.length > 0
  ? configuredBaseUrl
  : '/api/v1'
).replace(/\/+$/, '')

export const API_TIMEOUT_MS = 30_000
