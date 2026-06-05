import { api } from './client'
import type { TailscaleConfig } from '@/types'

export interface TailscaleUpdate {
  auth_key?: string
  login_server?: string | null
  enabled?: boolean
}

export const usersApi = {
  getTailscale: () => api.get<TailscaleConfig>('/users/me/tailscale'),
  updateTailscale: (payload: TailscaleUpdate) =>
    api.put<TailscaleConfig>('/users/me/tailscale', payload),
}
