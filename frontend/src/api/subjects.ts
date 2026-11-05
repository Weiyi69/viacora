import { api } from './client'
import type { Page, ResourceInput, Subject } from './learningTypes'

export type SubjectFilters = {
  q?: string
  includeArchived?: boolean
  page?: number
  pageSize?: number
}

export function getSubjects(filters: SubjectFilters = {}): Promise<Page<Subject>> {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.includeArchived) params.set('include_archived', 'true')
  if (filters.page) params.set('page', String(filters.page))
  if (filters.pageSize) params.set('page_size', String(filters.pageSize))
  const query = params.size ? `?${params.toString()}` : ''
  return api.get<Page<Subject>>(`/subjects${query}`)
}

export function getSubject(subjectId: string): Promise<Subject> {
  return api.get<Subject>(`/subjects/${subjectId}`)
}

export function createSubject(input: ResourceInput): Promise<Subject> {
  return api.post<Subject>('/subjects', input)
}

export function updateSubject(
  subjectId: string,
  input: Partial<ResourceInput>,
): Promise<Subject> {
  return api.patch<Subject>(`/subjects/${subjectId}`, input)
}

export function archiveSubject(subjectId: string): Promise<Subject> {
  return api.post<Subject>(`/subjects/${subjectId}/archive`)
}

export function restoreSubject(subjectId: string): Promise<Subject> {
  return api.post<Subject>(`/subjects/${subjectId}/restore`)
}

export function deleteSubject(subjectId: string): Promise<void> {
  return api.delete<void>(`/subjects/${subjectId}`)
}
