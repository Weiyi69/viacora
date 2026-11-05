import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from './auth'

const user = {
  id: 'c19f57c9-90c6-40da-a40b-8bcd0a2dd42f',
  email: 'learner@example.com',
  display_name: 'Learner',
  email_verified: true,
}

function jsonResponse(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => data,
  } as Response
}

describe('authentication store', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('restores session state from the server without browser token persistence', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({ authenticated: true, user }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const auth = useAuthStore()

    await auth.initialize()

    expect(auth.user).toEqual(user)
    expect(auth.isAuthenticated).toBe(true)
    expect(localStorage.length).toBe(0)
  })

  it('sets the user after login and clears it only after successful logout', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'before-login-csrf' }))
      .mockResolvedValueOnce(jsonResponse({ user }))
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'session-bound-csrf' }))
      .mockResolvedValueOnce(jsonResponse({ message: 'Logged out' }))
    vi.stubGlobal('fetch', fetchMock)
    const auth = useAuthStore()

    await auth.login(user.email, 'password')
    expect(auth.user).toEqual(user)

    await auth.logout()
    expect(auth.user).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
  })
})
