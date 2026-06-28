import { api } from './client'
import type { Zone, ZoneEnrollToken, ZoneOption } from '@/types'

export interface ZoneCreatePayload {
  name: string
  endpoint_host?: string
  endpoint_port?: number
}

export const zonesApi = {
  list: () => api.get<Zone[]>('/admin/zones'),
  // User-facing: enrolled zones (id + name) for the launch/migrate pickers.
  userList: () => api.get<ZoneOption[]>('/zones'),
  create: (payload: ZoneCreatePayload) => api.post<Zone>('/admin/zones', payload),
  update: (id: number, payload: Partial<ZoneCreatePayload> & { status?: string }) =>
    api.patch<Zone>(`/admin/zones/${id}`, payload),
  remove: (id: number) => api.delete(`/admin/zones/${id}`),
  enrollToken: (id: number) => api.post<ZoneEnrollToken>(`/admin/zones/${id}/enroll-token`, {}),
  rotateClientCert: (id: number) => api.post<Zone>(`/admin/zones/${id}/rotate-client-cert`, {}),
  // Push the control plane's current agent image to the zone and recreate its
  // agent on it. The agent briefly restarts.
  updateAgent: (id: number) =>
    api.post<{ status: string; detail: string }>(`/admin/zones/${id}/update-agent`, {}),
}
