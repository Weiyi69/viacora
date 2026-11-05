import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, apiUpload } from './client'

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

  it('uploads multipart data with credentials and CSRF without setting Content-Type', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({ csrf_token: 'upload-csrf-token' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const listeners = new Map<string, EventListener>()
    const uploadListeners = new Map<string, EventListener>()
    const requests: FakeXMLHttpRequest[] = []

    class FakeXMLHttpRequest {
      readonly upload = {
        addEventListener: (type: string, listener: EventListener) =>
          uploadListeners.set(type, listener),
      }
      readonly headers = new Headers()
      responseText = '{"id":"document-id"}'
      status = 201
      withCredentials = false
      method = ''
      url = ''
      body: Document | XMLHttpRequestBodyInit | null = null

      constructor() {
        requests.push(this)
      }

      open(method: string, url: string): void {
        this.method = method
        this.url = url
      }

      setRequestHeader(name: string, value: string): void {
        this.headers.set(name, value)
      }

      addEventListener(type: string, listener: EventListener): void {
        listeners.set(type, listener)
      }

      send(body: Document | XMLHttpRequestBodyInit | null): void {
        this.body = body
        uploadListeners.get('progress')?.({
          lengthComputable: true,
          loaded: 50,
          total: 100,
        } as ProgressEvent)
        listeners.get('load')?.(new ProgressEvent('load'))
      }
    }

    vi.stubGlobal('XMLHttpRequest', FakeXMLHttpRequest)
    const formData = new FormData()
    formData.append('file', new Blob(['%PDF-test']), 'test.pdf')
    const onProgress = vi.fn()

    await expect(apiUpload<{ id: string }>('/documents', formData, onProgress)).resolves.toEqual({
      id: 'document-id',
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(requests[0].method).toBe('POST')
    expect(requests[0].url).toBe('/api/v1/documents')
    expect(requests[0].withCredentials).toBe(true)
    expect(requests[0].headers.get('X-CSRF-Token')).toBe('upload-csrf-token')
    expect(requests[0].headers.has('Content-Type')).toBe(false)
    expect(requests[0].body).toBe(formData)
    expect(onProgress).toHaveBeenCalledWith(50)
  })
})
