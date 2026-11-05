import { api } from './client'
import type { Dashboard } from './learningTypes'

export function getDashboard(): Promise<Dashboard> {
  return api.get<Dashboard>('/dashboard')
}
