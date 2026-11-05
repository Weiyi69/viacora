export type User = {
  id: string
  email: string
  display_name: string
  email_verified: boolean
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

function apiUrl(path: string): string {
  return `${API_BASE_URL}/api/v1${path}`
}

async function csrfToken(): Promise<string> {
  const response = await fetch(apiUrl('/auth/csrf'), { credentials: 'include' })
  if (!response.ok) {
    throw new ApiError(response.status, 'Unable to start a secure request. Please try again.')
  }
  const payload = (await response.json()) as { csrf_token: string }
  return payload.csrf_token
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
  const headers = new Headers(options.headers)
  let token: string | undefined
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    token = await csrfToken()
    headers.set('X-CSRF-Token', token)
  }
  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(apiUrl(path), {
    ...options,
    method,
    headers,
    credentials: 'include',
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  if (!response.ok) {
    const payload = (await response.json().catch(() => undefined)) as
      | { detail?: string }
      | undefined
    throw new ApiError(
      response.status,
      payload?.detail ?? 'The request could not be completed. Please try again.',
    )
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string) => apiRequest<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiRequest<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body: unknown) =>
    apiRequest<T>(path, { method: 'PATCH', body }),
}
