import { api } from './client'
import type { Activity, Page } from './learningTypes'

export function getActivity(page = 1): Promise<Page<Activity>> {
  return api.get<Page<Activity>>(`/activity?page=${page}&page_size=20`)
}

export function recordAccess(
  resourceType: 'subject' | 'study_space',
  resourceId: string,
): Promise<Activity> {
  return api.post<Activity>('/activity/access', {
    resource_type: resourceType,
    resource_id: resourceId,
  })
}
