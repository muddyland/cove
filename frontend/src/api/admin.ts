import { api } from './client'
import type {
  User,
  Workspace,
  AuditEntry,
  AuditFacets,
  AuditQuery,
  AppSettings,
  EnvSummary,
  StorageInfo,
  PruneResult,
} from '@/types'

export const adminApi = {
  users: {
    list: () => api.get<User[]>('/admin/users'),
    create: (payload: { username: string; password: string; is_admin: boolean; docker_allowed?: boolean }) =>
      api.post<User>('/admin/users', payload),
    update: (id: number, payload: { username?: string; is_admin?: boolean; password?: string; docker_allowed?: boolean }) =>
      api.patch<User>(`/admin/users/${id}`, payload),
    remove: (id: number) => api.delete(`/admin/users/${id}`),
    // Sign the user out everywhere (invalidates every token issued before now).
    revokeSessions: (id: number) => api.post<void>(`/admin/users/${id}/revoke-sessions`),
  },
  sessions: {
    list: () => api.get<Workspace[]>('/admin/sessions'),
    kill: (id: number) => api.delete(`/admin/sessions/${id}`),
  },
  audit: {
    list: (q: AuditQuery = {}) => {
      const params = new URLSearchParams()
      for (const [k, v] of Object.entries(q)) {
        if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
      }
      const qs = params.toString()
      return api.get<AuditEntry[]>(`/admin/audit${qs ? `?${qs}` : ''}`)
    },
    facets: () => api.get<AuditFacets>('/admin/audit/facets'),
  },
  settings: {
    get: () => api.get<AppSettings>('/admin/settings'),
    update: (p: Partial<AppSettings>) => api.put<AppSettings>('/admin/settings', p),
  },
  env: {
    get: () => api.get<EnvSummary>('/admin/env'),
  },
  storage: {
    get: () => api.get<StorageInfo>('/admin/storage'),
    prune: (zone_id: number, deep: boolean) =>
      api.post<PruneResult>('/admin/storage/prune', { zone_id, deep }),
  },
}
