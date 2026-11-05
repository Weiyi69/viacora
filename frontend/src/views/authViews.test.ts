import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import LoginView from './LoginView.vue'
import RegisterView from './RegisterView.vue'
import ResetPasswordView from './ResetPasswordView.vue'

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  } as Response
}

function createTestRouter(path: string, component: typeof LoginView) {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path, component }],
  })
}

const globalStubs = { RouterLink: true }

describe('authentication views', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a safe login error response', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf' }))
      .mockResolvedValueOnce(
        jsonResponse({ detail: 'Email or password is incorrect' }, 401),
      )
    vi.stubGlobal('fetch', fetchMock)
    const router = createTestRouter('/login', LoginView)
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginView, {
      global: { plugins: [createPinia(), router], stubs: globalStubs },
    })

    await wrapper.find('input[autocomplete="email"]').setValue('learner@example.com')
    await wrapper.find('input[autocomplete="current-password"]').setValue('wrong-password')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain(
      'Email or password is incorrect',
    )
  })

  it('shows registration verification guidance after a successful request', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf' }))
      .mockResolvedValueOnce(jsonResponse({ message: 'Check your email' }, 201))
    vi.stubGlobal('fetch', fetchMock)
    const router = createTestRouter('/register', RegisterView)
    await router.push('/register')
    await router.isReady()
    const wrapper = mount(RegisterView, {
      global: { plugins: [createPinia(), router], stubs: globalStubs },
    })

    await wrapper.find('input[autocomplete="name"]').setValue('Learner')
    await wrapper.find('input[autocomplete="email"]').setValue('learner@example.com')
    await wrapper.find('input[autocomplete="new-password"]').setValue('a-secure-password-123')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="status"]').text()).toContain(
      'Check your email for a verification link',
    )
  })

  it('removes a reset token from the browser location before submitting it', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'csrf' }))
      .mockResolvedValueOnce(jsonResponse({ message: 'Password reset' }))
    vi.stubGlobal('fetch', fetchMock)
    const router = createTestRouter('/reset-password', ResetPasswordView)
    await router.push('/reset-password#token=private-one-time-token')
    await router.isReady()
    const wrapper = mount(ResetPasswordView, {
      global: { plugins: [createPinia(), router], stubs: globalStubs },
    })
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/reset-password')
    expect(router.currentRoute.value.fullPath).not.toContain('private-one-time-token')

    await wrapper.find('input[autocomplete="new-password"]').setValue('an-even-better-password')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue(
      'an-even-better-password',
    )
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const resetRequest = fetchMock.mock.calls[1][1]
    expect(resetRequest?.body).toContain('private-one-time-token')
    expect(wrapper.get('[role="status"]').text()).toContain('Password reset')
  })
})
