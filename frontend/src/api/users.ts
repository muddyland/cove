import { api } from './client'
import type { GluetunConfig, TailscaleConfig } from '@/types'

export interface TailscaleUpdate {
  auth_key?: string
  login_server?: string | null
  enabled?: boolean
}

export interface GluetunUpdate {
  enabled?: boolean
  vpn_type?: 'openvpn' | 'wireguard'
  config_file?: string | null
  config_filename?: string | null
  wireguard_private_key?: string | null
  openvpn_user?: string | null
  openvpn_password?: string | null
}

export const usersApi = {
  getTailscale: () => api.get<TailscaleConfig>('/users/me/tailscale'),
  updateTailscale: (payload: TailscaleUpdate) =>
    api.put<TailscaleConfig>('/users/me/tailscale', payload),
  getGluetun: () => api.get<GluetunConfig>('/users/me/gluetun'),
  updateGluetun: (payload: GluetunUpdate) =>
    api.put<GluetunConfig>('/users/me/gluetun', payload),
}
