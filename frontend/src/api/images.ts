import { api } from './client'
import type { WorkspaceImage } from '@/types'

export const imagesApi = {
  list: () => api.get<WorkspaceImage[]>('/images'),
  create: (payload: { name: string; docker_image: string; image_type: string; description?: string; internal_port?: number }) =>
    api.post<WorkspaceImage>('/images', payload),
  update: (id: number, payload: Partial<WorkspaceImage>) =>
    api.patch<WorkspaceImage>(`/images/${id}`, payload),
  remove: (id: number) => api.delete(`/images/${id}`),
  sync: () => api.post<{ added: number; updated: number; total: number }>('/images/sync'),
}
