import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  } as Response
}

describe('API client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('bootstraps CSRF and sends cookies and the CSRF header for state changes', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'signed-csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({ user: { id: 'user-id' } }))
    vi.stubGlobal('fetch', fetchMock)

    await api.post('/auth/login', { email: 'person@example.com', password: 'a long password' })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/auth/csrf', {
      credentials: 'include',
    })
    const [, options] = fetchMock.mock.calls[1]
    expect(options?.credentials).toBe('include')
    expect(options?.method).toBe('POST')
    expect(new Headers(options?.headers).get('X-CSRF-Token')).toBe('signed-csrf-token')
    expect(options?.body).toBe(
      JSON.stringify({ email: 'person@example.com', password: 'a long password' }),
    )
  })

  it('does not request a CSRF token for a safe request', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({ authenticated: false, user: null }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/auth/session')

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][1]?.credentials).toBe('include')
  })

  it('bootstraps CSRF for DELETE and accepts an empty success response', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({ csrf_token: 'delete-csrf-token' }),
    ).mockResolvedValueOnce({
      ok: true,
      status: 204,
      json: async () => undefined,
    } as Response)
    vi.stubGlobal('fetch', fetchMock)

    await api.delete('/subjects/subject-id')

    const [, options] = fetchMock.mock.calls[1]
    expect(options?.method).toBe('DELETE')
    expect(options?.credentials).toBe('include')
    expect(new Headers(options?.headers).get('X-CSRF-Token')).toBe('delete-csrf-token')
  })
})
