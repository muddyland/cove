import { api } from './client'
import type { LanPolicy, Workspace, WorkspaceStats } from '@/types'

export const workspacesApi = {
  list: () => api.get<Workspace[]>('/workspaces'),
  stats: () => api.get<Record<number, WorkspaceStats>>('/workspaces/stats'),
  lanPolicy: () => api.get<LanPolicy>('/workspaces/lan-policy'),
  get: (id: number) => api.get<Workspace>(`/workspaces/${id}`),
  create: (payload: {
    name: string
    image_id: number
    workspace_type: string
    target_url?: string
    kiosk?: boolean
    kiosk_dark?: boolean
    kiosk_menu?: boolean
    use_tailscale?: boolean
    lan_access?: boolean
    ts_exit_node?: string
    ts_accept_routes?: boolean
    ts_accept_dns?: boolean
    custom_dns?: boolean
    dns_servers?: string
    install_packages?: string
    proot_apps?: string
    appimages?: string
    allow_sudo?: boolean
  }) => api.post<Workspace>('/workspaces', payload),
  update: (
    id: number,
    payload: {
      name?: string
      target_url?: string
      kiosk?: boolean
      kiosk_dark?: boolean
      kiosk_menu?: boolean
      use_tailscale?: boolean
      lan_access?: boolean
      ts_exit_node?: string
      ts_accept_routes?: boolean
      ts_accept_dns?: boolean
      custom_dns?: boolean
      dns_servers?: string
      install_packages?: string
      proot_apps?: string
      appimages?: string
      allow_sudo?: boolean
    },
  ) => api.patch<Workspace>(`/workspaces/${id}`, payload),
  clone: (id: number, payload: { name: string; image_id?: number }) =>
    api.post<Workspace>(`/workspaces/${id}/clone`, payload),
  streamAuth: (id: number) => api.post<{ url: string }>(`/workspaces/${id}/stream-auth`),
  stop: (id: number) => api.post<Workspace>(`/workspaces/${id}/stop`),
  start: (id: number) => api.post<Workspace>(`/workspaces/${id}/start`),
  remove: (id: number, purgeStorage = false) =>
    api.delete(`/workspaces/${id}${purgeStorage ? '?purge_storage=true' : ''}`),
}
