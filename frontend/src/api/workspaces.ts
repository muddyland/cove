import { api } from './client'
import type { Workspace } from '@/types'

export const workspacesApi = {
  list: () => api.get<Workspace[]>('/workspaces'),
  get: (id: number) => api.get<Workspace>(`/workspaces/${id}`),
  create: (payload: {
    name: string
    image_id: number
    workspace_type: string
    target_url?: string
    use_tailscale?: boolean
    ts_exit_node?: string
    ts_accept_routes?: boolean
    ts_accept_dns?: boolean
  }) => api.post<Workspace>('/workspaces', payload),
  stop: (id: number) => api.post<Workspace>(`/workspaces/${id}/stop`),
  start: (id: number) => api.post<Workspace>(`/workspaces/${id}/start`),
  remove: (id: number) => api.delete(`/workspaces/${id}`),
}
