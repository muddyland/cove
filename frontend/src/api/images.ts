import { api } from './client'
import type { WorkspaceImage } from '@/types'

export const imagesApi = {
  list: () => api.get<WorkspaceImage[]>('/images'),
  create: (payload: { name: string; docker_image: string; image_type: string; description?: string; internal_port?: number }) =>
    api.post<WorkspaceImage>('/images', payload),
  update: (id: number, payload: Partial<WorkspaceImage>) =>
    api.patch<WorkspaceImage>(`/images/${id}`, payload),
  // Delete the catalog entry (global). With removeImage, also `docker image rm` it
  // on the given zone's daemon (zone 0 = local control plane).
  remove: (id: number, removeImage = false, zoneId = 0) =>
    api.delete(`/images/${id}?zone_id=${zoneId}${removeImage ? '&remove_image=true' : ''}`),
  // Delete the downloaded Docker image on a zone only, keeping the catalog entry.
  removeImageOnly: (id: number, zoneId = 0) => api.delete(`/images/${id}/image?zone_id=${zoneId}`),
  sync: () => api.post<{ added: number; updated: number; total: number }>('/images/sync'),
  // Download state is per-zone: the catalog is shared, but whether an image is
  // pulled (and the pull/remove actions) target one zone's daemon.
  pullStatus: (zoneId = 0) =>
    api.get<Record<number, ImagePullStatus>>(`/images/pull-status?zone_id=${zoneId}`),
  pull: (id: number, zoneId = 0) =>
    api.post<{ status: ImagePullStatus }>(`/images/${id}/pull?zone_id=${zoneId}`),
}

export type ImagePullStatus = 'present' | 'absent' | 'pulling'
