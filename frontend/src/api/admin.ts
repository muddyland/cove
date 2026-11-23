import { api } from './client'
import type {
  AdminWorkspaceCreate, AppSettings, AuditEntry, AuditFacets, AuditQuery, EnvSummary, NetworkCheck, PruneResult, StorageInfo, User,
  UserFormPayload, Workspace,
} from '@/types'

export const adminApi = {
  users: {
    list: () => api.get<User[]>('/admin/users'),
    create: (payload: UserFormPayload & { password: string }) => api.post<User>('/admin/users', payload),
    update: (id: number, payload: Partial<UserFormPayload>) => api.patch<User>(`/admin/users/${id}`, payload),
    remove: (id: number) => api.delete(`/admin/users/${id}`),
    // Sign the user out everywhere (invalidates every token issued before now).
    revokeSessions: (id: number) => api.post<void>(`/admin/users/${id}/revoke-sessions`),
    // Remove the user's authenticator app, passkeys and recovery codes, and sign them out.
    reset2fa: (id: number) => api.post<User>(`/admin/users/${id}/reset-2fa`),
    /** The account's own workspaces (for its sign-in workspace). */
    workspaces: (id: number) => api.get<Workspace[]>(`/admin/users/${id}/workspaces`),
    /** Set (and optionally lock) the workspace the account opens after sign-in. */
    setAutoLaunch: (id: number, workspaceId: string | null, locked: boolean) =>
      api.put<User>(`/admin/users/${id}/auto-launch`, { workspace_id: workspaceId, locked }),
    /** Create a workspace owned by the account (launched right away). */
    createWorkspace: (id: number, payload: AdminWorkspaceCreate) =>
      api.post<Workspace>(`/admin/users/${id}/workspaces`, payload),
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
  /** What Cove sees of this request: client address, LAN or not, proxy warnings. */
  networkCheck: () => api.get<NetworkCheck>('/admin/network-check'),
  storage: {
    get: () => api.get<StorageInfo>('/admin/storage'),
    prune: (zone_id: number, deep: boolean) =>
      api.post<PruneResult>('/admin/storage/prune', { zone_id, deep }),
  },
}
