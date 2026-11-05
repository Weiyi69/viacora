import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Dashboard, Page, Subject } from '../api/learningTypes'
import { getDashboard } from '../api/dashboard'
import { getSubjects } from '../api/subjects'
import HomeView from './HomeView.vue'

vi.mock('../api/dashboard', () => ({ getDashboard: vi.fn() }))
vi.mock('../api/subjects', () => ({
  createSubject: vi.fn(),
  getSubjects: vi.fn(),
}))

const emptyPage: Page<Subject> = {
  items: [],
  page: 1,
  page_size: 100,
  total: 0,
}

const emptyDashboard: Dashboard = {
  display_name: 'Learner',
  subject_count: 0,
  study_space_count: 0,
  subjects: [],
  recently_accessed: [],
  recent_activity: [],
}

async function mountDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: HomeView }],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(HomeView, {
    global: {
      plugins: [
        createPinia(),
        router,
        [VueQueryPlugin, { queryClient }],
      ],
      stubs: { RouterLink: true },
    },
  })
  return { wrapper, queryClient }
}

describe('learning dashboard', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows a loading state while the dashboard request is pending', async () => {
    let resolveDashboard: (dashboard: Dashboard) => void = () => undefined
    vi.mocked(getDashboard).mockReturnValue(
      new Promise((resolve) => {
        resolveDashboard = resolve
      }),
    )
    vi.mocked(getSubjects).mockResolvedValue(emptyPage)

    const { wrapper, queryClient } = await mountDashboard()
    await flushPromises()
    expect(wrapper.find('[aria-label="Loading content"]').exists()).toBe(true)

    resolveDashboard(emptyDashboard)
    await flushPromises()
    wrapper.unmount()
    queryClient.clear()
  })

  it('shows accurate zero counts and a useful first-subject empty state', async () => {
    vi.mocked(getDashboard).mockResolvedValue(emptyDashboard)
    vi.mocked(getSubjects).mockResolvedValue(emptyPage)

    const { wrapper, queryClient } = await mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('Welcome back, Learner.')
    expect(wrapper.text()).toContain('Start with a subject')
    expect(wrapper.text()).toContain('0')
    expect(wrapper.text()).not.toContain('streak')

    wrapper.unmount()
    queryClient.clear()
  })

  it('shows a retry state when dashboard data cannot be loaded', async () => {
    vi.mocked(getDashboard).mockRejectedValue(new Error('offline'))
    vi.mocked(getSubjects).mockResolvedValue(emptyPage)

    const { wrapper, queryClient } = await mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('Your dashboard could not load')
    expect(wrapper.findAll('button').map((button) => button.text())).toContain('Try again')

    wrapper.unmount()
    queryClient.clear()
  })
})
