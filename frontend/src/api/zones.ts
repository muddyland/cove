import { api } from './client'
import type { Zone, ZoneEnrollToken } from '@/types'

export interface ZoneCreatePayload {
  name: string
  endpoint_host?: string
  endpoint_port?: number
  stream_port?: number
}

export const zonesApi = {
  list: () => api.get<Zone[]>('/admin/zones'),
  create: (payload: ZoneCreatePayload) => api.post<Zone>('/admin/zones', payload),
  update: (id: number, payload: Partial<ZoneCreatePayload> & { status?: string }) =>
    api.patch<Zone>(`/admin/zones/${id}`, payload),
  remove: (id: number) => api.delete(`/admin/zones/${id}`),
  enrollToken: (id: number) => api.post<ZoneEnrollToken>(`/admin/zones/${id}/enroll-token`, {}),
  rotateClientCert: (id: number) => api.post<Zone>(`/admin/zones/${id}/rotate-client-cert`, {}),
}
