import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Page, Subject } from '../api/learningTypes'
import { createSubject, getSubjects } from '../api/subjects'
import SubjectsView from './SubjectsView.vue'

vi.mock('../api/subjects', () => ({
  archiveSubject: vi.fn(),
  createSubject: vi.fn(),
  deleteSubject: vi.fn(),
  getSubjects: vi.fn(),
  restoreSubject: vi.fn(),
  updateSubject: vi.fn(),
}))

const emptyPage: Page<Subject> = {
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
}

async function mountSubjects() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/subjects', component: SubjectsView }],
  })
  await router.push('/subjects')
  await router.isReady()
  const wrapper = mount(SubjectsView, {
    global: {
      plugins: [createPinia(), router, [VueQueryPlugin, { queryClient }]],
      stubs: { RouterLink: true },
    },
  })
  return { wrapper, queryClient }
}

describe('subjects screen', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows an empty state and creates a subject through the API', async () => {
    vi.mocked(getSubjects).mockResolvedValue(emptyPage)
    vi.mocked(createSubject).mockResolvedValue({
      id: 'subject-1',
      name: 'Biology',
      description: null,
      color: 'teal',
      icon: 'book',
      created_at: '',
      updated_at: '',
      archived_at: null,
      study_space_count: 0,
    })

    const { wrapper, queryClient } = await mountSubjects()
    await flushPromises()
    expect(wrapper.text()).toContain('Your library is ready')

    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'New subject')
      ?.trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('Create a subject')
    await wrapper.get('[role="dialog"] input').setValue('Biology')
    await wrapper.get('[role="dialog"] form').trigger('submit')
    await flushPromises()

    expect(createSubject).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Biology', color: 'blue', icon: 'book' }),
    )
    expect(wrapper.text()).toContain('Subject saved.')

    wrapper.unmount()
    queryClient.clear()
  })

  it('requests archived resources when the filter is enabled', async () => {
    vi.mocked(getSubjects).mockResolvedValue(emptyPage)

    const { wrapper, queryClient } = await mountSubjects()
    await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await flushPromises()

    expect(getSubjects).toHaveBeenLastCalledWith(
      expect.objectContaining({ includeArchived: true }),
    )

    wrapper.unmount()
    queryClient.clear()
  })
})
