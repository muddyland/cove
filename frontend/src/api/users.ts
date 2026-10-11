import { api } from './client'
import type { SshKeyConfig, TailscaleProfile, VpnProfile, VpnProvider, VpnType } from '@/types'

// Secrets use "omit to keep, '' to clear, value to replace".
export interface TailscaleProfileInput {
  name?: string
  auth_key?: string | null
  login_server?: string | null
}

export interface VpnProfileInput {
  name?: string
  mode?: 'custom' | 'provider'
  provider?: string | null
  vpn_type?: VpnType
  config_file?: string | null
  config_filename?: string | null
  wireguard_private_key?: string | null
  wireguard_preshared_key?: string | null
  wireguard_addresses?: string | null
  openvpn_user?: string | null
  openvpn_password?: string | null
  server_countries?: string | null
  server_cities?: string | null
  server_hostnames?: string | null
}

export const usersApi = {
  listTailscaleProfiles: () => api.get<TailscaleProfile[]>('/users/me/tailscale-profiles'),
  createTailscaleProfile: (payload: TailscaleProfileInput) =>
    api.post<TailscaleProfile>('/users/me/tailscale-profiles', payload),
  updateTailscaleProfile: (id: number, payload: TailscaleProfileInput) =>
    api.put<TailscaleProfile>(`/users/me/tailscale-profiles/${id}`, payload),
  deleteTailscaleProfile: (id: number) => api.delete<void>(`/users/me/tailscale-profiles/${id}`),

  listVpnProviders: () => api.get<VpnProvider[]>('/users/vpn-providers'),
  listVpnProfiles: () => api.get<VpnProfile[]>('/users/me/vpn-profiles'),
  createVpnProfile: (payload: VpnProfileInput) =>
    api.post<VpnProfile>('/users/me/vpn-profiles', payload),
  updateVpnProfile: (id: number, payload: VpnProfileInput) =>
    api.put<VpnProfile>(`/users/me/vpn-profiles/${id}`, payload),
  deleteVpnProfile: (id: number) => api.delete<void>(`/users/me/vpn-profiles/${id}`),

  getSshKey: () => api.get<SshKeyConfig>('/users/me/ssh'),
  uploadSshKey: (privateKey: string) =>
    api.put<SshKeyConfig>('/users/me/ssh', { private_key: privateKey }),
  generateSshKey: () => api.post<SshKeyConfig>('/users/me/ssh/generate'),
  deleteSshKey: () => api.delete<SshKeyConfig>('/users/me/ssh'),
}
