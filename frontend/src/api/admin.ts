import { api } from './client'
import type { User, Workspace, AuditEntry, AppSettings } from '@/types'

export const adminApi = {
  users: {
    list: () => api.get<User[]>('/admin/users'),
    create: (payload: { username: string; password: string; is_admin: boolean }) =>
      api.post<User>('/admin/users', payload),
    update: (id: number, payload: { username?: string; is_admin?: boolean; password?: string }) =>
      api.patch<User>(`/admin/users/${id}`, payload),
    remove: (id: number) => api.delete(`/admin/users/${id}`),
  },
  sessions: {
    list: () => api.get<Workspace[]>('/admin/sessions'),
    kill: (id: number) => api.delete(`/admin/sessions/${id}`),
  },
  audit: {
    list: () => api.get<AuditEntry[]>('/admin/audit'),
  },
  settings: {
    get: () => api.get<AppSettings>('/admin/settings'),
    update: (p: Partial<AppSettings>) => api.put<AppSettings>('/admin/settings', p),
  },
}
