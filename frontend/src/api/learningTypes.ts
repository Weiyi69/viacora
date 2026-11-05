export type Page<T> = {
  items: T[]
  page: number
  page_size: number
  total: number
}

export type Subject = {
  id: string
  name: string
  description: string | null
  color: string | null
  icon: string | null
  created_at: string
  updated_at: string
  archived_at: string | null
  study_space_count: number
}

export type StudySpace = {
  id: string
  subject_id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  archived_at: string | null
  topic_count: number
}

export type Topic = {
  id: string
  study_space_id: string
  name: string
  description: string | null
  position: number
  created_at: string
  updated_at: string
}

export type ActivityType =
  | 'subject_created'
  | 'study_space_created'
  | 'topic_created'
  | 'subject_accessed'
  | 'study_space_accessed'

export type Activity = {
  id: string
  event_type: ActivityType
  subject_id: string | null
  study_space_id: string | null
  topic_id: string | null
  resource_name: string
  occurred_at: string
}

export type Dashboard = {
  display_name: string
  subject_count: number
  study_space_count: number
  subjects: Subject[]
  recently_accessed: Activity[]
  recent_activity: Activity[]
}

export type ResourceInput = {
  name: string
  description?: string | null
  color?: string | null
  icon?: string | null
  position?: number
}
