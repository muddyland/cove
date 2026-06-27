import { api } from './client'

export interface DocEntry {
  slug: string
  title: string
}

export interface DocContent extends DocEntry {
  content: string
}

export const docsApi = {
  list: () => api.get<DocEntry[]>('/docs'),
  get: (slug: string) => api.get<DocContent>(`/docs/${encodeURIComponent(slug)}`),
}
