import { api } from './client'
import type {
  ContainerLogs, DiskUsage, DockerPolicy, GpuPolicy, LanPolicy, PublicIp, RuntimePolicy, SystemInfo,
  TailscaleStatus, Workspace, WorkspaceStats,
} from '@/types'

export type LogSource = 'desktop' | 'tailscale' | 'gluetun'

export const workspacesApi = {
  list: () => api.get<Workspace[]>('/workspaces'),
  stats: () => api.get<Record<number, WorkspaceStats>>('/workspaces/stats'),
  lanPolicy: () => api.get<LanPolicy>('/workspaces/lan-policy'),
  // GPU and Docker-in-Docker depend on the zone the workspace runs on.
  gpuPolicy: (zoneId = 0) => api.get<GpuPolicy>(`/workspaces/gpu-policy?zone_id=${zoneId}`),
  dockerPolicy: (zoneId = 0) => api.get<DockerPolicy>(`/workspaces/docker-policy?zone_id=${zoneId}`),
  runtimePolicy: () => api.get<RuntimePolicy>('/workspaces/runtime-policy'),
  extend: (id: number) => api.post<Workspace>(`/workspaces/${id}/extend`),
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
    tailscale_profile_id?: number
    gluetun_profile_id?: number
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
    use_docker?: boolean
    shared_profile?: boolean
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
      tailscale_profile_id?: number
      gluetun_profile_id?: number
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
      use_docker?: boolean
      shared_profile?: boolean
    },
  ) => api.patch<Workspace>(`/workspaces/${id}`, payload),
  clone: (id: number, payload: { name: string; image_id?: number }) =>
    api.post<Workspace>(`/workspaces/${id}/clone`, payload),
  migrate: (id: number, payload: { zone_id: number }) =>
    api.post<Workspace>(`/workspaces/${id}/migrate`, { target_zone_id: payload.zone_id }),
  info: (id: number) => api.get<SystemInfo>(`/workspaces/${id}/info`),
  diskUsage: (id: number) => api.get<DiskUsage>(`/workspaces/${id}/disk-usage`),
  publicIp: (id: number) => api.post<PublicIp>(`/workspaces/${id}/public-ip`),
  tailscaleStatus: (id: number) =>
    api.get<TailscaleStatus>(`/workspaces/${id}/tailscale-status`),
  logs: (id: number, source: LogSource, tail = 200) =>
    api.get<ContainerLogs>(`/workspaces/${id}/logs?source=${source}&tail=${tail}`),
  preview: (id: number) => api.getBlob(`/workspaces/${id}/preview.jpg`),
  refreshPreview: (id: number) =>
    api.post<{ captured: boolean; preview_at: string | null }>(`/workspaces/${id}/preview/refresh`),
  streamAuth: (id: number) => api.post<{ url: string }>(`/workspaces/${id}/stream-auth`),
  streamReady: (id: number) => api.get<{ ready: boolean }>(`/workspaces/${id}/stream-ready`),
  stop: (id: number) => api.post<Workspace>(`/workspaces/${id}/stop`),
  start: (id: number) => api.post<Workspace>(`/workspaces/${id}/start`),
  remove: (id: number, purgeStorage = false) =>
    api.delete(`/workspaces/${id}${purgeStorage ? '?purge_storage=true' : ''}`),
}
