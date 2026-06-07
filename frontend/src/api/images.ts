import { api } from './client'
import type { WorkspaceImage } from '@/types'

export const imagesApi = {
  list: () => api.get<WorkspaceImage[]>('/images'),
  create: (payload: { name: string; docker_image: string; image_type: string; description?: string; internal_port?: number }) =>
    api.post<WorkspaceImage>('/images', payload),
  update: (id: number, payload: Partial<WorkspaceImage>) =>
    api.patch<WorkspaceImage>(`/images/${id}`, payload),
  // Delete the catalog entry. With removeImage, also `docker image rm` it.
  remove: (id: number, removeImage = false) =>
    api.delete(`/images/${id}${removeImage ? '?remove_image=true' : ''}`),
  // Delete the local Docker image only, keeping the catalog entry.
  removeImageOnly: (id: number) => api.delete(`/images/${id}/image`),
  sync: () => api.post<{ added: number; updated: number; total: number }>('/images/sync'),
  pullStatus: () => api.get<Record<number, ImagePullStatus>>('/images/pull-status'),
  pull: (id: number) => api.post<{ status: ImagePullStatus }>(`/images/${id}/pull`),
}

export type ImagePullStatus = 'present' | 'absent' | 'pulling'
