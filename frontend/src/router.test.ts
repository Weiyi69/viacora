import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { router } from './router'

function sessionResponse(authenticated: boolean, emailVerified = false): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      authenticated,
      user: authenticated
        ? {
            id: 'user-id',
            email: 'learner@example.com',
            display_name: 'Learner',
            email_verified: emailVerified,
          }
        : null,
    }),
  } as Response
}

describe('authenticated routes', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('redirects signed-out users to login', async () => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValueOnce(sessionResponse(false)))

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('redirects unverified users to the verification screen', async () => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValueOnce(sessionResponse(true)))

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('verify-email')
  })

  it('protects learning and system-status pages behind authentication', async () => {
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValueOnce(sessionResponse(false)))

    await router.push('/subjects')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/subjects')
    expect(
      router.options.routes
        .filter((route) => route.name !== 'login' && route.name !== 'register')
        .filter((route) =>
          [
            'home',
            'subjects',
            'subject-detail',
            'study-space-detail',
            'documents',
            'document-detail',
            'system-status',
          ].includes(String(route.name)),
        )
        .every((route) => route.meta?.requiresAuth),
    ).toBe(true)
  })
})
