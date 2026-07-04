import { api } from './client'
import type { ContainerLogs, GpuPolicy, LanPolicy, TailscaleStatus, Workspace, WorkspaceStats } from '@/types'

export type LogSource = 'desktop' | 'tailscale' | 'gluetun'

export const workspacesApi = {
  list: () => api.get<Workspace[]>('/workspaces'),
  stats: () => api.get<Record<number, WorkspaceStats>>('/workspaces/stats'),
  lanPolicy: () => api.get<LanPolicy>('/workspaces/lan-policy'),
  gpuPolicy: () => api.get<GpuPolicy>('/workspaces/gpu-policy'),
  get: (id: number) => api.get<Workspace>(`/workspaces/${id}`),
  create: (payload: {
    name: string
    image_id: number
    workspace_type: string
    zone_id?: number
    target_url?: string
    kiosk?: boolean
    kiosk_dark?: boolean
    kiosk_menu?: boolean
    use_tailscale?: boolean
    use_gluetun?: boolean
    ephemeral?: boolean
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
    inject_ssh_key?: boolean
    pixelflux_wayland?: boolean
    clear_browser_lock?: boolean
    gpu_accel?: boolean
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
      use_gluetun?: boolean
      ephemeral?: boolean
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
      inject_ssh_key?: boolean
      pixelflux_wayland?: boolean
      clear_browser_lock?: boolean
      gpu_accel?: boolean
    },
  ) => api.patch<Workspace>(`/workspaces/${id}`, payload),
  clone: (id: number, payload: { name: string; image_id?: number }) =>
    api.post<Workspace>(`/workspaces/${id}/clone`, payload),
  migrate: (id: number, payload: { zone_id: number }) =>
    api.post<Workspace>(`/workspaces/${id}/migrate`, { target_zone_id: payload.zone_id }),
  tailscaleStatus: (id: number) =>
    api.get<TailscaleStatus>(`/workspaces/${id}/tailscale-status`),
  logs: (id: number, source: LogSource, tail = 200) =>
    api.get<ContainerLogs>(`/workspaces/${id}/logs?source=${source}&tail=${tail}`),
  streamAuth: (id: number) => api.post<{ url: string }>(`/workspaces/${id}/stream-auth`),
  streamReady: (id: number) => api.get<{ ready: boolean }>(`/workspaces/${id}/stream-ready`),
  stop: (id: number) => api.post<Workspace>(`/workspaces/${id}/stop`),
  start: (id: number) => api.post<Workspace>(`/workspaces/${id}/start`),
  remove: (id: number, purgeStorage = false) =>
    api.delete(`/workspaces/${id}${purgeStorage ? '?purge_storage=true' : ''}`),
}
