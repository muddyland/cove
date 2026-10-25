import { api } from './client'
import type {
  ThinClient,
  ThinClientCommand,
  ThinClientCommandKind,
  ThinClientEnrollToken,
  ThinClientPolicy,
  ThinClientSelf,
  ThinClientWorkspaceOption,
} from '@/types'

const base = '/admin/thin-clients'

export const thinClientsApi = {
  list: () => api.get<ThinClient[]>(base),
  get: (id: number) => api.get<ThinClient>(`${base}/${id}`),
  create: (payload: { name: string; location?: string }) => api.post<ThinClient>(base, payload),
  update: (id: number, payload: { name?: string; location?: string; policy?: ThinClientPolicy }) =>
    api.patch<ThinClient>(`${base}/${id}`, payload),
  remove: (id: number) => api.delete(`${base}/${id}`),
  enrollToken: (id: number, ttlHours = 1) =>
    api.post<ThinClientEnrollToken>(`${base}/${id}/enroll-token`, { ttl_hours: ttlHours }),
  commands: (id: number) => api.get<ThinClientCommand[]>(`${base}/${id}/commands`),
  command: (id: number, kind: ThinClientCommandKind, args: Record<string, unknown> = {}) =>
    api.post<ThinClientCommand>(`${base}/${id}/commands`, { kind, args }),
  signOut: (id: number) => api.post<ThinClient>(`${base}/${id}/signout`, {}),
  keepAwake: (id: number, minutes: number) => api.post<ThinClient>(`${base}/${id}/keep-awake`, { minutes }),
  screenshot: (id: number) => api.getBlob(`${base}/${id}/screenshot.png`),
  workspaceOptions: () => api.get<ThinClientWorkspaceOption[]>(`${base}/workspace-options`),
  // Not admin: which thin client this browser runs on (404 on a normal browser).
  self: () => api.get<ThinClientSelf>('/thin-client/self'),
}
