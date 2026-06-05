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
    install_packages?: string
    proot_apps?: string
    allow_sudo?: boolean
  }) => api.post<Workspace>('/workspaces', payload),
  update: (
    id: number,
    payload: {
      name?: string
      target_url?: string
      use_tailscale?: boolean
      ts_exit_node?: string
      ts_accept_routes?: boolean
      ts_accept_dns?: boolean
      install_packages?: string
      proot_apps?: string
      allow_sudo?: boolean
    },
  ) => api.patch<Workspace>(`/workspaces/${id}`, payload),
  stop: (id: number) => api.post<Workspace>(`/workspaces/${id}/stop`),
  start: (id: number) => api.post<Workspace>(`/workspaces/${id}/start`),
  remove: (id: number) => api.delete(`/workspaces/${id}`),
}
