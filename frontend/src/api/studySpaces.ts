import { api } from './client'
import type { Page, ResourceInput, StudySpace } from './learningTypes'

export type StudySpaceFilters = {
  q?: string
  sortBy?: 'recent' | 'name'
  includeArchived?: boolean
  page?: number
  pageSize?: number
}

export function getStudySpaces(
  subjectId: string,
  filters: StudySpaceFilters = {},
): Promise<Page<StudySpace>> {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.sortBy) params.set('sort_by', filters.sortBy)
  if (filters.includeArchived) params.set('include_archived', 'true')
  if (filters.page) params.set('page', String(filters.page))
  if (filters.pageSize) params.set('page_size', String(filters.pageSize))
  const query = params.size ? `?${params.toString()}` : ''
  return api.get<Page<StudySpace>>(
    `/subjects/${subjectId}/study-spaces${query}`,
  )
}

export function getStudySpace(studySpaceId: string): Promise<StudySpace> {
  return api.get<StudySpace>(`/study-spaces/${studySpaceId}`)
}

export function createStudySpace(
  subjectId: string,
  input: ResourceInput,
): Promise<StudySpace> {
  return api.post<StudySpace>(
    `/subjects/${subjectId}/study-spaces`,
    input,
  )
}

export function updateStudySpace(
  studySpaceId: string,
  input: Partial<ResourceInput>,
): Promise<StudySpace> {
  return api.patch<StudySpace>(`/study-spaces/${studySpaceId}`, input)
}

export function archiveStudySpace(studySpaceId: string): Promise<StudySpace> {
  return api.post<StudySpace>(`/study-spaces/${studySpaceId}/archive`)
}

export function restoreStudySpace(studySpaceId: string): Promise<StudySpace> {
  return api.post<StudySpace>(`/study-spaces/${studySpaceId}/restore`)
}

export function deleteStudySpace(studySpaceId: string): Promise<void> {
  return api.delete<void>(`/study-spaces/${studySpaceId}`)
}
