import { api } from './client'
import type { Page, ResourceInput, Topic } from './learningTypes'

export function getTopics(
  studySpaceId: string,
  query?: string,
  page = 1,
  pageSize = 50,
): Promise<Page<Topic>> {
  const params = new URLSearchParams()
  if (query) params.set('q', query)
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
  const search = params.size ? `?${params.toString()}` : ''
  return api.get<Page<Topic>>(
    `/study-spaces/${studySpaceId}/topics${search}`,
  )
}

export function createTopic(
  studySpaceId: string,
  input: ResourceInput,
): Promise<Topic> {
  return api.post<Topic>(`/study-spaces/${studySpaceId}/topics`, input)
}

export function updateTopic(
  topicId: string,
  input: Partial<ResourceInput>,
): Promise<Topic> {
  return api.patch<Topic>(`/topics/${topicId}`, input)
}

export function deleteTopic(topicId: string): Promise<void> {
  return api.delete<void>(`/topics/${topicId}`)
}
