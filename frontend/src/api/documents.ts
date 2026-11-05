import { api, apiUpload } from './client'
import type { Page } from './learningTypes'

export type DocumentStatus = 'queued' | 'processing' | 'completed' | 'failed'

export type LearningDocument = {
  id: string
  subject_id: string
  study_space_id: string
  topic_id: string | null
  original_filename: string
  file_size_bytes: number
  status: DocumentStatus
  attempts_used: number
  retryable: boolean
  error_code: string | null
  page_count: number | null
  has_extractable_text: boolean
  created_at: string
  updated_at: string
  processed_at: string | null
}

export type DocumentLibrary = Page<LearningDocument> & {
  storage_used_bytes: number
  storage_quota_bytes: number
}

export type DocumentPage = {
  page_number: number
  extracted_text: string
}

export type DocumentFilters = {
  subjectId?: string
  studySpaceId?: string
  topicId?: string
  status?: DocumentStatus
  q?: string
  page?: number
  pageSize?: number
}

function documentQuery(filters: DocumentFilters = {}): string {
  const params = new URLSearchParams()
  if (filters.subjectId) params.set('subject_id', filters.subjectId)
  if (filters.studySpaceId) params.set('study_space_id', filters.studySpaceId)
  if (filters.topicId) params.set('topic_id', filters.topicId)
  if (filters.status) params.set('status', filters.status)
  if (filters.q) params.set('q', filters.q)
  params.set('page', String(filters.page ?? 1))
  params.set('page_size', String(filters.pageSize ?? 20))
  return params.toString()
}

export function getDocuments(filters: DocumentFilters = {}): Promise<DocumentLibrary> {
  return api.get<DocumentLibrary>(`/documents?${documentQuery(filters)}`)
}

export function getDocument(documentId: string): Promise<LearningDocument> {
  return api.get<LearningDocument>(`/documents/${documentId}`)
}

export function getDocumentPages(
  documentId: string,
  page = 1,
  pageSize = 10,
): Promise<Page<DocumentPage>> {
  return api.get<Page<DocumentPage>>(
    `/documents/${documentId}/pages?page=${page}&page_size=${pageSize}`,
  )
}

export function uploadDocument(
  subjectId: string,
  studySpaceId: string,
  file: File,
  topicId: string | null,
  onProgress: (progress: number) => void,
): Promise<LearningDocument> {
  const body = new FormData()
  body.append('file', file)
  if (topicId) body.append('topic_id', topicId)
  return apiUpload<LearningDocument>(
    `/subjects/${subjectId}/study-spaces/${studySpaceId}/documents`,
    body,
    onProgress,
  )
}

export function assignDocumentTopic(
  documentId: string,
  topicId: string | null,
): Promise<LearningDocument> {
  return api.patch<LearningDocument>(`/documents/${documentId}`, { topic_id: topicId })
}

export function retryDocument(documentId: string): Promise<LearningDocument> {
  return api.post<LearningDocument>(`/documents/${documentId}/retry`)
}

export function deleteDocument(documentId: string): Promise<void> {
  return api.delete<void>(`/documents/${documentId}`)
}
