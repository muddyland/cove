import { api } from './client'

/** Who a document is written for. Admin-scoped docs are filtered out of the
 *  listing for non-admins, and fetching one directly returns `403`. */
export type DocScope = 'user' | 'admin'

export interface DocEntry {
  slug: string
  title: string
  scope: DocScope
}

export interface DocContent extends DocEntry {
  content: string
}

export const docsApi = {
  list: () => api.get<DocEntry[]>('/docs'),
  get: (slug: string) => api.get<DocContent>(`/docs/${encodeURIComponent(slug)}`),
}
